# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import logging
import os
import boto3
import json
import pandas as pd
import awswrangler as wr

from botocore.exceptions import ClientError

METADATA_DATABASE = os.environ['metadata_database']
BUCKET = os.environ['metadata_bucket']
CATALOG_ID = os.environ['account_id']
SECURITY_LAKE_DB = os.environ['security_lake_db']
SECURITY_LAKE_TABLE = os.environ['security_lake_table']
ACCOUNT_METADATA_TABLE = 'aws_account_metadata'
OU_TABLE = 'ou_groups'
TAGS_TABLE = 'tags_groups'

GROUP_BY_TAG = ''

# set up logging for lambda
if len(logging.getLogger().handlers) > 0:
    logging.getLogger().setLevel(logging.INFO)
else:  
    logger = logging.getLogger(__name__)
    logging.basicConfig(
        format='%(asctime)s %(message)s',
        level=logging.INFO,
        datefmt='%Y-%m-%d %H:%M:%S'
    )

# set up clients
lf_client = boto3.client('lakeformation')
athena_client = boto3.client('athena')
s3_client = boto3.client('s3')

def get_account_metadata(session=None):
    
    # if we don't get a session, create one
    if session is None:
        session = boto3.Session()
    
    organizations = session.client('organizations')

    # get org-id
    response = organizations.describe_organization()
    org_info = response.get("Organization")
    
    def get_org_root():
        response = organizations.list_roots()
        roots = response.get("Roots", None)
    
        if not roots:
            return None
    
        return roots[0]
    
    def get_tags(id):
        paginator = organizations.get_paginator("list_tags_for_resource")
        responses = paginator.paginate(ResourceId=id)
    
        tags = {}
        for response in responses:
            for tag in response.get("Tags", []):
                tags[tag["Key"]] = tag["Value"]
    
        return tags
    
    def get_child_orgunits(parent_id):
        paginator = organizations.get_paginator("list_organizational_units_for_parent")
        responses = paginator.paginate(ParentId=parent_id)
    
        orgunits = []
        for response in responses:
            orgunits += response.get("OrganizationalUnits", [])
    
        return orgunits
        
    def get_child_accounts(parent_id):
        paginator = organizations.get_paginator("list_accounts_for_parent")
        responses = paginator.paginate(ParentId=parent_id)
    
        accounts = []
        for response in responses:
            accounts += response.get("Accounts", [])
    
        return accounts
    
    accounts_list = {}
    accounts_list['Accounts'] = []
    
    ou_list = []
    
    def walk_org(orgunit_id, depth):
        child_accounts = get_child_accounts(orgunit_id)
    
        for account in child_accounts:
            child_account_id = account["Id"]
            tags = get_tags(child_account_id)
            
            # make sure all tag keys are lower case
            tags = dict((k.lower(), v) for k,v in tags.items())

            account_info = {}
            account_info['id'] = child_account_id
            account_info['name'] = account['Name']
            account_info['org_id'] = org_info['Id']
            account_info['ou'] = depth
            account_info['ou_id'] = orgunit_id
            account_info['tags'] = tags
            logging.info("Found account: "+json.dumps(account_info))
            accounts_list['Accounts'].append(account_info)
    
        child_orgunits = get_child_orgunits(orgunit_id)
        for orgunit in child_orgunits:
            child_orgunit_id = orgunit["Id"]
    
            # lookup orgunit details
            response = organizations.describe_organizational_unit(OrganizationalUnitId=child_orgunit_id)
            name = response["OrganizationalUnit"]["Name"]
            tags = get_tags(child_orgunit_id)
            
            ou_list.append(depth+',OU='+name)
            
            walk_org(child_orgunit_id, depth+',OU='+name)

              
    org_root = get_org_root()
    org_root_id = org_root.get("Id")
    ou_list.append("OU=root")
    walk_org(org_root_id, "OU=root")
    
    return accounts_list, ou_list

def get_data_cells_filter_names(catalog_id, database_name, table_name):
    paginator = lf_client.get_paginator("list_data_cells_filter")
    responses = paginator.paginate(Table={
        'CatalogId': catalog_id,
        'DatabaseName': database_name,
        'Name': table_name
    })
    
    data_cells_filters_list = []
    for response in responses:
        for data_cells_filter in response['DataCellsFilters']:
            data_cells_filters_list.append(data_cells_filter['Name'])
        
    return data_cells_filters_list
    
def create_metadata_table(database, table_name, df_table, bucket):
    # check if table exists
    if wr.catalog.does_table_exist(database=database, table=table_name):
        # remove old account metadata
        logging.info('--- Dropping '+table_name+' table ---')
        wr.athena.read_sql_query(
            sql=f'DROP TABLE `{table_name}`',
            database=database,
            ctas_approach=False,
            unload_approach=False,
        )
    
    # write table to S3
    logging.info('--- Creating '+table_name+' table ---')
    df_table = df_table.astype(str)
    
    wr.athena.to_iceberg(
        df_table,
        database=database,
        table=table_name,
        table_location='s3://'+bucket+'/'+table_name+'/',
        temp_path='s3://'+bucket+'/'+table_name+'/temp/',
        keep_files=False
    )

    # write metadata to Iceberg table
    logging.info('--- Creating grant for '+table_name+' table ---')

    
def lambda_handler(event, context):

    logging.info('--- Getting Metadata ---')
    account_metadata, ou_metadata = get_account_metadata()
    df_account_metadata = pd.json_normalize(account_metadata['Accounts'])

    df_ou_metadata = pd.DataFrame({'ou':ou_metadata})
    df_ou_metadata['consumer_aws_account_id'] = ''          
    
    # write metadata iceberg table (static)
    create_metadata_table(METADATA_DATABASE, ACCOUNT_METADATA_TABLE, df_account_metadata, BUCKET)
    
    # check if OU table exists and merge existing consumer_aws_account_id to ou_id mappings
    # this allows us to add new OUs without having to update the whole table
    if wr.catalog.does_table_exist(database=METADATA_DATABASE, table=OU_TABLE):
        # get the table
        df_existing_table = wr.athena.read_sql_query(
            sql=f'SELECT * FROM "{OU_TABLE}"',
            database=METADATA_DATABASE,
            ctas_approach=False,
            unload_approach=False,
        )
        
        logging.info('--- Found existing OU grouping table ---') 
        logging.debug(df_existing_table)
        
        logging.info('--- Mapping existing account ids to OU groups ---')
        df_new_table = pd.merge(df_ou_metadata, df_existing_table, left_on='ou', right_on='ou', how='left').drop('consumer_aws_account_id_x', axis=1)
        df_ou_metadata = df_new_table.rename(columns={'consumer_aws_account_id_y': 'consumer_aws_account_id'})
                
        logging.debug("new table:")
        logging.debug(df_ou_metadata)
        
        
        ## drop existing table
        logging.info('--- Dropping old table ---')
        wr.athena.read_sql_query(
            sql=f'DROP TABLE `{OU_TABLE}`',
            database=METADATA_DATABASE,
            ctas_approach=False,
            unload_approach=False,
        )
    
    
        response = s3_client.list_objects_v2(Bucket=BUCKET, Prefix=OU_TABLE+'/')
        logging.info('--- Removing '+OU_TABLE+' table metadata ---')
        for object in response['Contents']:
            logging.debug('Deleting', object['Key'])
            s3_client.delete_object(Bucket=BUCKET, Key=object['Key'])
        
    # write table to S3
    logging.info('--- Creating new '+OU_TABLE+' Iceberg table ---')
    logging.debug(df_ou_metadata)
    
    wr.athena.to_iceberg(
        df_ou_metadata,
        database=METADATA_DATABASE,
        table=OU_TABLE,
        table_location='s3://'+BUCKET+'/'+OU_TABLE+'/',
        temp_path='s3://'+BUCKET+'/'+OU_TABLE+'/temp/',
    )
    
    # only set up sharing if we have groups to share to
    df_ou_metadata = df_ou_metadata.replace(r'^s*$', float('NaN'), regex = True)
    df_ou_metadata = df_ou_metadata.dropna()
    if not df_ou_metadata.empty:
    
        # get all data cells filters for the account_metadata table
        data_cells_filters_account_metadata = get_data_cells_filter_names(CATALOG_ID, METADATA_DATABASE, ACCOUNT_METADATA_TABLE)
        
        # get data cells filters for SL table
        data_cells_filters_security_lake = get_data_cells_filter_names(CATALOG_ID, SECURITY_LAKE_DB, SECURITY_LAKE_TABLE)
        
        # build the SQL for data filters
        for index, row in df_ou_metadata.iterrows():
            data_filter = ""
            df_accounts_in_group = df_account_metadata.query('ou.str.contains("'+row['ou']+'")', engine='python')
            for account_index, account_row in df_accounts_in_group.iterrows():
                data_filter = data_filter+"id='"+str(account_row['id'])+"' OR "
        
            data_filter = data_filter.rstrip(" OR ") 
            
            # if the data cells filter already exists we can just update the filter
            if 'account_metadata_filter_'+str(row['ou']).replace(',','_').replace('=','_') in data_cells_filters_account_metadata:
                logging.info('--- Updating existing data cells filter for '+row['ou']+' = '+str(row['consumer_aws_account_id'])+' ---')
                response = lf_client.update_data_cells_filter(
                    TableData={
                        'TableCatalogId': CATALOG_ID,
                        'DatabaseName': METADATA_DATABASE,
                        'TableName': ACCOUNT_METADATA_TABLE,
                        'Name': 'account_metadata_filter_'+str(row['ou']).replace(',','_').replace('=','_'),
                        'RowFilter': {
                            'FilterExpression': data_filter
                        },
                        'ColumnWildcard': {}
                    }
                )
            
            else:
                logging.info('--- Creating data cells filter for '+row['ou']+' = '+str(row['consumer_aws_account_id'])+' ---')
                response = lf_client.create_data_cells_filter(
                    TableData={
                        'TableCatalogId': CATALOG_ID,
                        'DatabaseName': METADATA_DATABASE,
                        'TableName': ACCOUNT_METADATA_TABLE,
                        'Name': 'account_metadata_filter_'+str(row['ou']).replace(',','_').replace('=','_'),
                        'RowFilter': {
                            'FilterExpression': data_filter
                        },
                        'ColumnWildcard': {}
                    }
                )
            
            # create cross-account grants for account_metadata
            logging.info('--- Creating grant for account_metadata table to account '+str(row['consumer_aws_account_id'])+' ---')
            response = lf_client.batch_grant_permissions(
                CatalogId=CATALOG_ID,
                Entries=[
                    {
                        'Id': 'account-share-'+str(row['consumer_aws_account_id']),
                        'Principal': {
                            'DataLakePrincipalIdentifier': str(row['consumer_aws_account_id'])
                        },
                        'Resource': {
                            'DataCellsFilter': {
                                'TableCatalogId': CATALOG_ID,
                                'DatabaseName': METADATA_DATABASE,
                                'TableName': ACCOUNT_METADATA_TABLE,
                                'Name': 'account_metadata_filter_'+str(row['ou']).replace(',','_').replace('=','_')
                            }
                        },
                        'Permissions': [
                            'SELECT',
                        ],
                        'PermissionsWithGrantOption': [
                            'SELECT',
                        ]
                    },
                ]
            )
            
            if 'security_lake_filter_'+str(row['ou']).replace(',','_').replace('=','_') in data_cells_filters_security_lake:
                logging.info('--- Updating existing data cells filter for SL '+row['ou']+' = '+str(row['consumer_aws_account_id'])+' ---')
                response = lf_client.update_data_cells_filter(
                    TableData={
                        'TableCatalogId': CATALOG_ID,
                        'DatabaseName': SECURITY_LAKE_DB,
                        'TableName': SECURITY_LAKE_TABLE,
                        'Name': 'security_lake_filter_'+str(row['ou']).replace(',','_').replace('=','_'),
                        'RowFilter': {
                            'FilterExpression': data_filter.replace('id','accountid')
                        },
                        'ColumnWildcard': {}
                    }
                )
            else:
                logging.info('--- Creating data cells filter for SL '+row['ou']+' = '+str(row['consumer_aws_account_id'])+' ---')
                response = lf_client.create_data_cells_filter(
                    TableData={
                        'TableCatalogId': CATALOG_ID,
                        'DatabaseName': SECURITY_LAKE_DB,
                        'TableName': SECURITY_LAKE_TABLE,
                        'Name': 'security_lake_filter_'+str(row['ou']).replace(',','_').replace('=','_'),
                        'RowFilter': {
                            'FilterExpression': data_filter.replace('id','accountid')
                        },
                        'ColumnWildcard': {}
                    }
                )
            
            # create cross-account grants for account_metadata
            logging.info('--- Creating grant for SL table to account '+str(row['consumer_aws_account_id'])+' ---')
            response = lf_client.batch_grant_permissions(
                CatalogId=CATALOG_ID,
                Entries=[
                    {
                        'Id': 'account-share-'+str(row['consumer_aws_account_id']),
                        'Principal': {
                            'DataLakePrincipalIdentifier': str(row['consumer_aws_account_id'])
                        },
                        'Resource': {
                            'DataCellsFilter': {
                                'TableCatalogId': CATALOG_ID,
                                'DatabaseName': SECURITY_LAKE_DB,
                                'TableName': SECURITY_LAKE_TABLE,
                                'Name': 'security_lake_filter_'+str(row['ou']).replace(',','_').replace('=','_'),
                            }
                        },
                        'Permissions': [
                            'SELECT',
                        ],
                        'PermissionsWithGrantOption': [
                            'SELECT',
                        ]
                    },
                ]
            )
    

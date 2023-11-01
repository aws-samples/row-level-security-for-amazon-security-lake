
# Row-Level Security for Amazon Security Lake
A tool to help share Amazon Security Lake data per Organizational Unit (OU) using [row-level security features](https://docs.aws.amazon.com/lake-formation/latest/dg/data-filtering.html) of AWS Lake Formation.

## Concept

This solution helps AWS customers share subsets of Security Lake data to different teams within their organization. It groups account data based on Organizational Unit (OU) metadata available within an AWS Organization. It leverages **row-level security** functionality of AWS Lake Formation to securely share tables to AWS accounts that you specify. A key benefit of this solution is that it uses available metadata to automate the grouping of accounts and secure sharing of tables, making it easy to deploy at-scale.

![Distributed Amazon Security Lake Data](/imgs/distributed-security-lake.png "Distributed Amazon Security Lake Data")

## Distributed ownership use case
It can be common to find security ownership distributed across an AWS Organization. Take for example a parent company with legal entities operating under it who are responsible for the security posture of the AWS accounts within their line of business. Not only is each entity accountable for managing and reporting on security within their area, they must not be able to view security data of other entities within the same AWS Organization.

These entities need a way of aggregating Security Lake data from only their AWS accounts into a single place that can be reviewed and tracked. Customers want to be able to group accounts using their business context, for example by department name, application identifier or OU.

We also find customers often implement a distributed cost model where they prefer analytics costs be owned by teams that require the security insights rather than centrally. This solution ensures most of the data processing and visualising costs are pushed out to the accounts consuming the data.

## Architecture
The solution is made up of a few components.

![Distributed Amazon Security Lake Data Architecture](/imgs/distributed-security-lake-lambda.png "Distributed Amazon Security Lake Data Architecture")

- An AWS Lambda function
    - runs in an Security Lake delegated administrator account
    - periodically gets OU metadata from the AWS Organization and writes it to a table
    - groups accounts by OUs and creates row-level security filters
    - shares metadata and Security Lake tables to other AWS accounts through AWS Lake Formation.

- Data consumer AWS account
    - accepts resource access manager share that contains filtered tables
    - joins metadata and Security Lake tables for enrichment
    - develops QuickSight dashboards to visualise security posture

## Grouping security accounts
The solution supports grouping accounts by OU (and eventually tags).

- OU groups are maintained within the `ou_groups` table and require you to specify the `consumer_account_id` - the AWS account id that you want to share the group data with. You can see the OUs that were discovered by using Athena and running the following query:
```
SELECT * FROM aws_account_metadata_db.ou_groups
```
You can then set the `consumer_account_id` for an OU by:

```
UPDATE aws_account_metadata_db.ou_groups
SET consumer_account_id = '123456789012'
WHERE ou = 'OU=root,OU=WhateverOUYouWant'
```
**Notes:**
- The solution currently only support a single AWS account as a consumer
- If an OU group does not have a `consumer_account_id`, that group will not be shared.

# Deployment overview
- Run cdk app to deploy into Security Lake delegated admin
- Review `ou_groups` table and set `consumer_account_id`
- Re-run Lambda
- Login to each consumer AWS account and accept RAM invitation
- Create resource links for shared tables
- Create Athena View to join datasets
- Configure QuickSight to visualise data

## CDK app deployment

# Install CDK for python
```
$ python -m pip install aws-cdk-lib
```

# Do the virtual env stuff

```
$ python -m venv .venv
$ source .venv/bin/activate
$ pip install -r requirements.txt

```

Edit the `cdk.context.json` file to specify:

- **"metadata_database"**: the name you want to give the metadata DB. Default: **aws_account_metadata_db**
- **"security_lake_db"**: the Security Lake DB as registered by Security Lake. Default: **amazon_security_lake_glue_db_ap_southeast_2**
- **"security_lake_table"**: the Security Lake table you want to share. Default: **amazon_security_lake_table_ap_southeast_2_sh_findings_1_0**


# Deploy the app

```
$ cdk deploy
```

# Clean up
You can remove the app using:
```
$ cdk destroy
```
You also need to remove any Lake Formation [data cells filters](https://console.aws.amazon.com/lakeformation/home?#data-filters) created by the solution.

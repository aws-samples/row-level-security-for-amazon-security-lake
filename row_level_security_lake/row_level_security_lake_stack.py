# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from aws_cdk import (
    Stack,
    CfnParameter,
    aws_lambda as _lambda,
    aws_s3 as _s3,
    aws_glue as _glue,
    aws_iam as _iam,
    aws_lakeformation as _lakeformation,
    aws_events as _events,
    aws_events_targets as _targets,
)
import aws_cdk as cdk
from constructs import Construct
from aws_cdk.custom_resources import Provider
from cdk_nag import NagSuppressions

class RowLevelSecurityLakeStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # create CfnParameters to handle deployment through CloudFormation
        cf_param_metadata_db = CfnParameter(self, "MetadataDatabase",
            type="String",
            default=self.node.try_get_context('metadata_database'),
            description="The name of the database used to store account metadata."
            )
    
        cf_param_security_lake_db = CfnParameter(self, "SecurityLakeDB",
            type="String",
            default=self.node.try_get_context('security_lake_db'),
            description="The name of the Security Lake database specified in the Glue Catalog."
            )
        
        cf_param_security_lake_table = CfnParameter(self, "SecurityLakeTable",
            type="String",
            default=self.node.try_get_context('security_lake_table'),
            description="The name of the Security Lake table you want to share."
            )

        # bucket to store AWS account and groups metadata
        metadata_bucket = _s3.Bucket(
            self,
            'metadata_bucket',
            enforce_ssl=True,
            removal_policy=cdk.RemovalPolicy.DESTROY,
            auto_delete_objects=True
        )
        
        # register bucket with LakeFormation
        cfn_resource = _lakeformation.CfnResource(self, "LFRegisteredBucket",
            resource_arn=metadata_bucket.bucket_arn,
            use_service_linked_role=True
        )

        # permissions needed for RLSecLake lambda
        rl_sec_lake_lambda_role = _iam.Role(self, "RLSecLakeLambdaRole",
            assumed_by=_iam.ServicePrincipal("lambda.amazonaws.com"),
            inline_policies={
                "OrgsAccess": _iam.PolicyDocument(
                    statements=[_iam.PolicyStatement(
                        actions=[
                            "organizations:ListRoots",
                            "organizations:ListTagsForResource",
                            "organizations:ListOrganizationalUnitsForParent",
                            "organizations:ListAccountsForParent",
                            "organizations:DescribeOrganizationalUnit",
                            "organizations:DescribeOrganization"
                        ],
                        resources=[
                            "*"
                            ]
                    )]
                    ),
                "S3Access": _iam.PolicyDocument(
                    statements=[_iam.PolicyStatement(
                        actions=[
                            "s3:GetObject",
                            "s3:PutObject",
                            "s3:DeleteObject",
                            "s3:ListBucket",
                        ],
                        resources=[
                            metadata_bucket.bucket_arn+"/*",
                            metadata_bucket.bucket_arn,
                            f"arn:aws:s3:::aws-athena-query-results-{Stack.of(self).account}-{Stack.of(self).region}/*",
                            ]
                    )]
                    ),
                "LakeFormationAccess": _iam.PolicyDocument(
                    statements=[_iam.PolicyStatement(
                        actions=[
                            "lakeformation:ListDataCellsFilter",
                            "lakeformation:ListPermissions",
                            "lakeformation:ListResources",
                            "lakeformation:GetDataAccess",
                            "lakeformation:BatchGrantPermissions",
                            "lakeformation:CreateDataCellsFilter",
                            "lakeformation:UpdateDataCellsFilter",
                            "lakeformation:DeleteDataCellsFilter",
                        ],
                        resources=[
                            "*"
                            ]
                    )]
                    ),
                "GlueMetadataTableAccess": _iam.PolicyDocument(
                    statements=[_iam.PolicyStatement(
                        actions=[
                            "glue:CreateTable",
                            "glue:DeleteTable",
                            "glue:GetTable",
                            "glue:UpdateTable",
                            "glue:PutResourcePolicy",
                            "glue:BatchGetTable",
                        ],
                        resources=[
                            f"arn:aws:glue:{Stack.of(self).region}:{Stack.of(self).account}:catalog",
                            f"arn:aws:glue:{Stack.of(self).region}:{Stack.of(self).account}:database/{cf_param_metadata_db.value_as_string}",
                            f"arn:aws:glue:{Stack.of(self).region}:{Stack.of(self).account}:table/{cf_param_metadata_db.value_as_string}/*"
                            ]
                        )
                    ]
                ),
                "GlueSecLakeTableAccess": _iam.PolicyDocument(
                    statements=[_iam.PolicyStatement(
                        actions=[
                            "glue:GetTable",
                            "glue:UpdateTable",
                            "glue:PutResourcePolicy",
                            "glue:BatchGetTable",
                        ],
                        resources=[
                            f"arn:aws:glue:{Stack.of(self).region}:{Stack.of(self).account}:database/{cf_param_security_lake_db.value_as_string}",
                            f"arn:aws:glue:{Stack.of(self).region}:{Stack.of(self).account}:table/{cf_param_security_lake_db.value_as_string}/{cf_param_security_lake_table.value_as_string}"
                            ]
                        )
                    ]
                ),
                "AthenaAccess": _iam.PolicyDocument(
                    statements=[_iam.PolicyStatement(
                        actions=[
                            "athena:StartQueryExecution",
                            "athena:GetQueryExecution",
                            "athena:GetQueryResults",
                            "s3:CreateBucket",
                            "s3:GetBucketLocation"
                        ],
                        resources=[
                            f"arn:aws:athena:{Stack.of(self).region}:{Stack.of(self).account}:workgroup/*",
                            f"arn:aws:s3:::aws-athena-query-results-{Stack.of(self).account}-{Stack.of(self).region}",
                            ]
                        )
                    ]
                ),
                "RAMAccess": _iam.PolicyDocument(
                    statements=[_iam.PolicyStatement(
                        actions=[
                            "ram:CreateResourceShare",
                            "ram:GetResourceShares",
                            "ram:AssociateResourceShare",
                        ],
                        resources=[
                            "*"
                            ]
                        )
                    ]
                ),
                "LambdaLogging": _iam.PolicyDocument(
                    statements=[_iam.PolicyStatement(
                        actions=[
                            "logs:CreateLogGroup",
                            "logs:CreateLogStream",
                            "logs:PutLogEvents"
                        ],
                        resources=[
                            f"arn:aws:logs:{Stack.of(self).region}:{Stack.of(self).account}:log-group:/aws/lambda/RLSecLake*"
                            ]
                        )
                    ]
                ),
                }
        )

        # create metadata database
        metadata_database = _glue.CfnDatabase(self, 'RLSecLakeDatabase',
            catalog_id=cdk.Aws.ACCOUNT_ID,
            database_input=_glue.CfnDatabase.DatabaseInputProperty(
                name=cf_param_metadata_db.value_as_string,
                description='Stores AWS account metadata and group information'
            ),    
        )

        # create RLSecLake lambda
        rl_sec_lake_lambda = _lambda.Function(
            self, 'RLSecLakeLambda',
            function_name='RLSecLakeLambda',
            runtime=_lambda.Runtime.PYTHON_3_11,
            code=_lambda.Code.from_asset('lambda/rl_sec_lake/'),
            handler='lambda_function.lambda_handler',
            layers=[
                _lambda.LayerVersion.from_layer_version_arn(self, 'PandasLayer', layer_version_arn=f'arn:aws:lambda:{Stack.of(self).region}:336392948345:layer:AWSSDKPandas-Python311:1')
            ],
            environment={
                'metadata_bucket': metadata_bucket.bucket_name,
                'metadata_database': cf_param_metadata_db.value_as_string,
                'account_id': Stack.of(self).account,
                'security_lake_db': cf_param_security_lake_db.value_as_string,
                'security_lake_table': cf_param_security_lake_table.value_as_string,
            },
            role=rl_sec_lake_lambda_role,
            timeout=cdk.Duration.minutes(5),
            memory_size=512
        )

        # permissions needed for custom resource lambda
        custom_resource_lambda_role = _iam.Role(self, "CustomResourceLambdaRole",
            assumed_by=_iam.ServicePrincipal("lambda.amazonaws.com"),
            inline_policies={
                "LambdaInvoke": _iam.PolicyDocument(
                    statements=[_iam.PolicyStatement(
                        actions=[
                            "lambda:InvokeFunction"
                        ],
                        resources=[
                            rl_sec_lake_lambda.function_arn
                            ]
                    )]
                    ),
                    "LambdaLogging": _iam.PolicyDocument(
                    statements=[_iam.PolicyStatement(
                        actions=[
                            "logs:CreateLogGroup",
                            "logs:CreateLogStream",
                            "logs:PutLogEvents"
                        ],
                        resources=[
                            f"arn:aws:logs:{Stack.of(self).region}:{Stack.of(self).account}:log-group:/aws/lambda/RLSecLake*"
                            ]
                        )
                    ]
                ),
            }
        )

        # create custom resource lambda to trigger our RLSecLake lambda on create
        custom_resource_lambda = _lambda.Function(
            self, 'CustomResourceLambda',
            function_name='RLSecLakeCustomResourceLambda',
            runtime=_lambda.Runtime.PYTHON_3_11,
            code=_lambda.Code.from_asset('lambda/custom_resource/'),
            handler='lambda_function.on_event',
            role=custom_resource_lambda_role,
            environment={
                'rlseclake_lambda_arn': rl_sec_lake_lambda.function_arn
            },
            timeout=cdk.Duration.minutes(5),
            memory_size=512
        )

        provider = Provider(scope=self, 
            id='RLSecLakeProvider', 
            on_event_handler=custom_resource_lambda
            )

        cdk.CustomResource(
            scope=self,
            id='RLSecLakeCustomResource',
            service_token=provider.service_token,
            removal_policy=cdk.RemovalPolicy.DESTROY,
            resource_type="Custom::RLSecLakeCustomResource"
        )

        # eventbridge schedule trigger to run lambda every hour
        rule = _events.Rule(self, "LambdaTriggerRule",
            schedule=_events.Schedule.rate(cdk.Duration.hours(1))
        )

        rule.add_target(_targets.LambdaFunction(rl_sec_lake_lambda))

        # CDK needs ability to manage permissions in Lake Formation
        admin_permissions = _lakeformation.CfnDataLakeSettings(
            self, "DataLakeSettings",
            admins=[
                _lakeformation.CfnDataLakeSettings.DataLakePrincipalProperty(
                    data_lake_principal_identifier=f"arn:aws:iam::{Stack.of(self).account}:role/cdk-hnb659fds-cfn-exec-role-{Stack.of(self).account}-{Stack.of(self).region}"),
                ])

        rl_sec_lake_db_permissions = _lakeformation.CfnPermissions(self, "DBPrincipalPermissions",
            data_lake_principal=_lakeformation.CfnPermissions.DataLakePrincipalProperty(
                data_lake_principal_identifier=rl_sec_lake_lambda_role.role_arn
            ),
            permissions=[
                            'ALL',
                            'ALTER',
                            'CREATE_TABLE',
                            'DESCRIBE',
                            'DROP'
                        ],
            permissions_with_grant_option=[
                            'ALL',
                            'ALTER',
                            'CREATE_TABLE',
                            'DESCRIBE',
                            'DROP'
                        ],
            resource=_lakeformation.CfnPermissions.ResourceProperty(
                database_resource=_lakeformation.CfnPermissions.DatabaseResourceProperty(
                    catalog_id=cdk.Aws.ACCOUNT_ID,
                    name=cf_param_metadata_db.value_as_string
                ),
                )
            )
        rl_sec_lake_db_permissions.add_dependency(metadata_database)
        rl_sec_lake_db_permissions.add_dependency(admin_permissions)

        rl_sec_lake_s3_permissions = _lakeformation.CfnPermissions(self, "MetadataBucketLocationPermissions",
            data_lake_principal=_lakeformation.CfnPermissions.DataLakePrincipalProperty(
                data_lake_principal_identifier=rl_sec_lake_lambda_role.role_arn
            ),
            resource=_lakeformation.CfnPermissions.ResourceProperty(
                data_location_resource=_lakeformation.CfnPermissions.DataLocationResourceProperty(
                    catalog_id=cdk.Aws.ACCOUNT_ID,
                    s3_resource=metadata_bucket.bucket_arn
                ),
            ),
            permissions=["DATA_LOCATION_ACCESS"],
        )
        
        # rl_sec_lake_s3_permissions.add_dependency(metadata_bucket)
        rl_sec_lake_s3_permissions.add_dependency(admin_permissions)

        rl_sec_lake_sec_lake_permissions = _lakeformation.CfnPermissions(self, "ExistingSecLakeTablePermissions",
            data_lake_principal=_lakeformation.CfnPermissions.DataLakePrincipalProperty(
                data_lake_principal_identifier=rl_sec_lake_lambda_role.role_arn
            ),
            resource=_lakeformation.CfnPermissions.ResourceProperty(
                table_resource=_lakeformation.CfnPermissions.TableResourceProperty(
                    catalog_id=cdk.Aws.ACCOUNT_ID,
                    database_name=cf_param_security_lake_db.value_as_string,
                    name=cf_param_security_lake_table.value_as_string
                ),
            ),
            permissions=["SELECT"],
            permissions_with_grant_option=["SELECT"]
        )

        rl_sec_lake_sec_lake_permissions.add_dependency(admin_permissions)

        ### CDK-Nag Suppression Rules ###
        NagSuppressions.add_resource_suppressions(metadata_bucket,
            [
                {
                    'id':"AwsSolutions-S1",
                    'reason':"S3 access logging not required for metadata bucket"
                }
            ]
        )
        NagSuppressions.add_resource_suppressions(rl_sec_lake_lambda_role,
            [
                {
                'id':'AwsSolutions-IAM5',
                'reason': "Wildcards are constrained with prefixes applicable to the solution - eg RLSecLake*",
                }
            ]
        )
        NagSuppressions.add_resource_suppressions(custom_resource_lambda_role,
            [
                {
                'id':'AwsSolutions-IAM5',
                'reason': "Only uses wildcards for CloudWatch Logs and are constrained with prefixes applicable to the solution - eg RLSecLake*",
                }
            ]
        )
        NagSuppressions.add_resource_suppressions(provider,
            [
                {
                'id':'AwsSolutions-IAM4',
                'reason': "Default behaviour of provider construct used for custom resource. Only used for Lambda logging.",
                },
                {
                'id':'AwsSolutions-IAM5',
                'reason': "Default behaviour of provider construct used for custom resource."
                }
            ],
            True
        )
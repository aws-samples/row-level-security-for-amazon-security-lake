# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import boto3
import os
import json

RLSECLAKE_LAMBDA_ARN = os.environ['rlseclake_lambda_arn']

client = boto3.client('lambda')  

def on_event(event, context):
    print(event)
    request_type = event['RequestType'].lower()
    if request_type == 'create':
        return on_create(event)
    if request_type == 'update':
        return on_update(event)
    if request_type == 'delete':
        return on_delete(event)
    raise Exception(f'Invalid request type: {request_type}')

def on_create(event):
    response = client.invoke(
        FunctionName=RLSECLAKE_LAMBDA_ARN,
        InvocationType='Event'
    )
    return {'Output': json.loads(json.dumps(response, default=str))}

def on_update(event):
    return

def on_delete(event):
    return



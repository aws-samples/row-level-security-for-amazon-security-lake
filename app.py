# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

#!/usr/bin/env python3
import aws_cdk as cdk
from aws_cdk import Aspects, DefaultStackSynthesizer
from cdk_nag import AwsSolutionsChecks

from row_level_security_lake.row_level_security_lake_stack import RowLevelSecurityLakeStack


app = cdk.App()
RowLevelSecurityLakeStack(app, "RowLevelSecurityLakeStack",
                          
    synthesizer=DefaultStackSynthesizer(
        generate_bootstrap_version_rule=False
    )
)

# cdk-nag checks
Aspects.of(app).add(AwsSolutionsChecks())
app.synth()

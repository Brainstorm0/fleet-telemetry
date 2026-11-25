# Real-Time IoT Fleet Telemetry & Predictive Anomaly Dashboard

**Form title:** Real-Time Cloud Telemetry Ingestion and Anomaly Tracking System Using Event-Driven Architecture

Simulated trucks stream GPS, speed, engine and cargo-temperature data to AWS. A Lambda function
verifies each signed message, detects anomalies (including a *predicted* temperature breach before
it happens), stores everything in DynamoDB, emails supervisors through SNS, and pushes live updates
to a web dashboard over WebSockets.

**Full step-by-step instructions are in `Execution_Guide.pdf`.**

## Folder layout
```
lambdas/processor/    Kinesis consumer: HMAC check, anomaly detection, DynamoDB, SNS, WebSocket push
lambdas/connections/  WebSocket $connect / $disconnect / ping
lambdas/api/          REST API: /latest /history /alerts /health
simulator/            5 simulated trucks (modes: dry-run, local, kinesis, iot)
local_demo/           Runs the whole pipeline on your laptop (no AWS)
dashboard/            Live map, vehicle table, temperature chart, alert feed
infra/                Terraform for AWS Academy Learner Lab (uses LabRole)
tests/                PyTest unit tests
.github/workflows/    CI/CD pipeline (lint, test, terraform checks, deploy)
docs/                 Architecture, SLA, Costing, Security
```

## Quick start - local (no AWS)
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
pytest                                   # 19 tests should pass
python local_demo/server.py              # terminal 1, then open http://localhost:8000
python simulator/simulator.py --mode local   # terminal 2
```

## Quick start - AWS Learner Lab
```bash
# 1. Start the lab, copy the AWS CLI credentials into ~/.aws/credentials
cd infra
cp terraform.tfvars.example terraform.tfvars   # put your email in it
terraform init
terraform apply                          # type "yes"; takes about 5-10 minutes
cd ..
python simulator/simulator.py --mode iot        # or --mode kinesis
# open the dashboard_url printed by terraform
```
When you are finished: `cd infra && terraform destroy` (this stops the Kinesis charge).

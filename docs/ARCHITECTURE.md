# Architecture - Real-Time IoT Fleet Telemetry & Predictive Anomaly Dashboard

**Cloud pattern:** serverless, event-driven (no servers to manage, pay per use).

```
 Simulated trucks (Python)
   |  MQTT over TLS 1.2, X.509 cert          (or HTTPS PutRecords in "kinesis" mode)
   v
 AWS IoT Core  --IoT Rule: SELECT * FROM 'fleet/telemetry/+'-->  Amazon Kinesis Data Stream (1 shard, 24 h replay)
                                                                     |  event source mapping (batch 50)
                                                                     v
                                                 Lambda "telemetry-processor"
                                                 1. verify HMAC-SHA256 signature
                                                 2. validate schema
                                                 3. detect anomalies (threshold, z-score, trend forecast)
                                                   |              |                    |
                                                   v              v                    v
                                     DynamoDB FleetTelemetry   Amazon SNS        API Gateway WebSocket
                                     (VehicleId + Timestamp)   (email alerts)    (post_to_connection)
                                     DynamoDB FleetState                                |
                                     (latest / alerts)                                  v
                                            ^                              Dashboard (HTML/JS, Leaflet, Chart.js)
                                            |  GET /latest /history /alerts       private S3 -> site Lambda -> HTTP API (HTTPS)
                                     Lambda "rest-api" <-- API Gateway HTTP API <------'
```

## Services and why they were chosen

| Layer | Service | Role |
|---|---|---|
| Ingestion | AWS IoT Core | Managed MQTT broker, per-device X.509 identity |
| Buffer | Amazon Kinesis Data Streams | Ordered per vehicle, 24 h replay after failures |
| Compute | AWS Lambda (Python 3.12) x4 | Processor, WebSocket connection manager, REST API, dashboard site |
| Database | Amazon DynamoDB x3 | Telemetry (PK VehicleId, SK Timestamp), state, connections |
| Real-time | API Gateway WebSocket API | Pushes new readings to open dashboards |
| REST | API Gateway HTTP API | Initial page load and history |
| Notifications | Amazon SNS | Email to supervisors on critical alerts (5-min cooldown) |
| Hosting | Amazon S3 + Lambda + API Gateway HTTP API | Private bucket, HTTPS, security headers. CloudFront (CDN) in front is optional; Learner Lab blocks it |
| Monitoring | CloudWatch logs, metrics, alarms, dashboard | Errors, iterator age, tampering attempts |
| IaC | Terraform | Whole stack in code (fast rebuild = low RTO) |
| CI/CD | GitHub Actions | Lint, tests, terraform checks, deploy |

## Anomaly detection
1. **Threshold rules:** cargo > 8 C (critical), engine > 110 C (critical), door open while moving (critical),
   speed > 90 km/h, fuel < 10 %, cargo < -25 C.
2. **Statistical:** z-score of the new cargo reading against the vehicle's last 20 readings (|z| >= 3.5).
3. **Predictive:** least-squares line through recent temperatures. If the rise is steady (R^2 >= 0.8),
   fast enough (>= 0.5 C/min) and the forecast crosses 8 C, a *PREDICTED_TEMP_BREACH* warning is raised
   before the cargo is actually spoiled. The horizon is capped at 5x the history span to avoid wild guesses.
   Tested on 60,000 simulated healthy readings with zero false predictions.

## Learner Lab adaptations
* No IAM roles can be created, so all Lambdas and the IoT rule use the existing **LabRole**.
* Region **us-east-1**. Credentials are temporary and must be refreshed each lab session.
* `enable_iot` / `enable_cloudfront` switches allow a fallback if a service is blocked in your lab.
* **CloudFront is blocked** (`CreateDistribution`, Origin Access Control and Origin Access Identity are all denied).
  The dashboard is therefore served by a small `dashboard-site` Lambda behind the HTTP API: it reads an allow-list
  of files from the private bucket and returns them over HTTPS with security headers. With
  `enable_cloudfront = true` (outside the lab) CloudFront is added in front of that API as a CDN.
* **The dashboard bucket is created with the AWS CLI** (a `terraform_data` resource) instead of `aws_s3_bucket`:
  the lab denies `s3:GetBucketObjectLockConfiguration`, which the Terraform AWS provider calls on every read.
  Its encryption, public-access block and files are still managed by Terraform.
* **Kinesis retention stays at 24 h.** The lab denies `kinesis:IncreaseStreamRetentionPeriod`; if a first apply
  fails on it, run `terraform untaint aws_kinesis_stream.telemetry` and apply again.

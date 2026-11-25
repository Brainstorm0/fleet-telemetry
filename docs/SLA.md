# Service Level Agreement (SLA)

| Item | Target | How the design supports it |
|---|---|---|
| Service availability | **99.90 %** monthly (max ~43.8 min downtime / month) | Only multi-AZ managed services (IoT Core, Kinesis, Lambda, DynamoDB, API Gateway, CloudFront) |
| Ingestion loss tolerance | **< 0.001 %** dropped messages | MQTT QoS 1, Kinesis durable storage, Lambda retries (3) with batch bisect and partial-batch failure reporting |
| RPO (data loss window) | **~0 s** | Kinesis keeps every record 24 h, so a failed consumer re-reads from where it stopped; DynamoDB point-in-time recovery (35 days) |
| RTO (time to recover) | **< 3 min** for code/config faults | Redeploy the last good Lambda version, or `terraform apply` (a full rebuild takes about 5-10 min, mostly CloudFront) |
| End-to-end latency SLO | **< 1.5 s** from device to dashboard (95th percentile) | Batching window 0 s, one WebSocket push per batch; the dashboard shows the live latency |
| Alert notification | Critical alert email within **60 s** | SNS publish happens in the same Lambda run |

## Monitoring that enforces the SLA
* `fleet-processor-errors` alarm - Lambda errors >= 1 for 3 minutes.
* `fleet-kinesis-iterator-age` alarm - processing more than 60 s behind (latency SLO at risk).
* `fleet-invalid-signatures` alarm - 5+ tampered frames in 5 minutes.
* CloudWatch dashboard `fleet-fleet-telemetry` - incoming records, duration, anomalies, iterator age.
* `GET /health` endpoint for uptime probes.

## Incident recovery procedure
1. **Detect:** the CloudWatch alarm emails the SNS subscribers.
2. **Triage:** open the CloudWatch dashboard, then the Lambda logs (`/aws/lambda/fleet-telemetry-processor`).
3. **Bad deployment:** in the Lambda console, use *Versions* to roll back, or revert the commit (CI redeploys).
4. **Consumer stopped:** fix and redeploy; Kinesis replays the missed records automatically (24 h window).
5. **Data corruption:** restore the DynamoDB table with point-in-time recovery to a new table and switch the env var.
6. **Full region / stack loss:** run `terraform apply` from the repository (Infrastructure as Code).
7. **Post-incident:** write a short report (cause, impact, fix, prevention).

> Note: AWS Academy Learner Lab sessions stop after a few hours, and that downtime is a lab limit,
> not part of the SLA design. The SLA describes the production design.

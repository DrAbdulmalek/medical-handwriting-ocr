# -----------------------------------------------------------------------------
# Monitoring Module - Outputs
# -----------------------------------------------------------------------------

output "alarm_topic_arn" {
  description = "ARN of the SNS topic used for alarm notifications"
  value       = aws_sns_topic.alarms.arn
}

output "log_group_names" {
  description = "List of CloudWatch log group names created by this module"
  value       = aws_cloudwatch_log_group.app[*].name
}

output "log_group_arns" {
  description = "List of CloudWatch log group ARNs created by this module"
  value       = aws_cloudwatch_log_group.app[*].arn
}

output "dashboard_name" {
  description = "Name of the CloudWatch dashboard (null if not created)"
  value       = var.enable_dashboard ? local.dashboard_name : null
}

output "alarm_arns" {
  description = "Map of alarm names to their ARNs"
  value = {
    high_error_rate   = aws_cloudwatch_metric_alarm.high_error_rate.arn
    high_latency      = aws_cloudwatch_metric_alarm.high_latency.arn
    low_health_score  = aws_cloudwatch_metric_alarm.low_health_score.arn
  }
}

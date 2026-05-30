# -----------------------------------------------------------------------------
# CloudWatch Monitoring Module
# -----------------------------------------------------------------------------
# Provides observability for the Medical Handwriting OCR project, including:
#   - SNS topic for alert notifications (email subscriptions)
#   - CloudWatch dashboard with OCR-specific metrics widgets
#   - Log groups for backend, Celery workers, and Nginx
#   - Alarms for high error rate, high latency, and low health score
# -----------------------------------------------------------------------------

locals {
  # Standard tags merged with caller-supplied extras
  common_tags = merge(
    {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
      Module      = "monitoring"
    },
    var.tags
  )

  # SNS topic name: use custom name if provided, otherwise auto-generate
  sns_topic_name = var.sns_topic_name != "" ? var.sns_topic_name : "${var.project_name}-${var.environment}-alarms"

  # Default log group names if none specified
  resolved_log_group_names = length(var.log_group_names) > 0 ? var.log_group_names : [
    "/aws/eks/${var.project_name}/backend",
    "/aws/eks/${var.project_name}/celery",
    "/aws/eks/${var.project_name}/nginx",
  ]

  dashboard_name = "${var.project_name}-${var.environment}-ocr-dashboard"
}

# =============================================================================
# SNS Topic for Alerts
# =============================================================================
resource "aws_sns_topic" "alarms" {
  name = local.sns_topic_name

  tags = local.common_tags
}

# Email subscriptions
resource "aws_sns_topic_subscription" "email" {
  count    = length(var.alarm_email_addresses)
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email_addresses[count.index]
}

# =============================================================================
# CloudWatch Log Groups
# =============================================================================
resource "aws_cloudwatch_log_group" "app" {
  count = length(local.resolved_log_group_names)

  name              = local.resolved_log_group_names[count.index]
  retention_in_days = var.default_log_retention_days

  tags = local.common_tags
}

# =============================================================================
# CloudWatch Alarms
# =============================================================================

# -- High Error Rate Alarm ----------------------------------------------------
# Triggers when the 5xx error rate exceeds the configured threshold
resource "aws_cloudwatch_metric_alarm" "high_error_rate" {
  alarm_name          = "${var.project_name}-${var.environment}-high-error-rate"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = var.alarm_evaluation_periods
  metric_name         = "5XXError"
  namespace           = "AWS/ApplicationELB"
  period              = var.alarm_period_seconds
  statistic           = "Sum"
  threshold           = var.high_error_rate_threshold
  alarm_description   = "High error rate detected in the Application Load Balancer"

  dimensions = {}  # Will be wired to the specific LB in a real deployment

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]

  tags = local.common_tags
}

# -- High Latency Alarm -------------------------------------------------------
# Triggers when the target response time exceeds the configured threshold
resource "aws_cloudwatch_metric_alarm" "high_latency" {
  alarm_name          = "${var.project_name}-${var.environment}-high-latency"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = var.alarm_evaluation_periods
  metric_name         = "TargetResponseTime"
  namespace           = "AWS/ApplicationELB"
  period              = var.alarm_period_seconds
  statistic           = "Average"
  threshold           = var.high_latency_threshold
  alarm_description   = "High latency detected: API response time exceeds ${var.high_latency_threshold}ms"

  dimensions = {}

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]

  tags = local.common_tags
}

# -- Low Health Score Alarm ----------------------------------------------------
# Triggers when a custom OCR health check metric drops below the threshold
resource "aws_cloudwatch_metric_alarm" "low_health_score" {
  alarm_name          = "${var.project_name}-${var.environment}-low-health-score"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = var.alarm_evaluation_periods
  metric_name         = "HealthScore"
  namespace           = "MedicalOCR/${var.project_name}"
  period              = var.alarm_period_seconds
  statistic           = "Average"
  threshold           = var.low_health_score_threshold
  alarm_description   = "OCR application health score dropped below ${var.low_health_score_threshold}"

  dimensions = {
    Environment = var.environment
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]

  tags = local.common_tags
}

# =============================================================================
# CloudWatch Dashboard
# =============================================================================
resource "aws_cloudwatch_dashboard" "ocr" {
  count = var.enable_dashboard ? 1 : 0

  dashboard_name = local.dashboard_name

  dashboard_body = jsonencode({
    widgets = [
      # -- OCR Pipeline Metrics ------------------------------------------------
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title = "OCR Pipeline - Processing Rate"
          view  = "timeSeries"
          stacked = false
          metrics = [
            ["MedicalOCR/${var.project_name}", "DocumentsProcessed", "Environment", var.environment, { stat = "Sum" }],
            ["MedicalOCR/${var.project_name}", "ProcessingErrors", "Environment", var.environment, { stat = "Sum" }],
          ]
          region = var.region
          period = 300
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          title = "OCR Pipeline - Latency"
          view  = "timeSeries"
          stacked = false
          metrics = [
            ["MedicalOCR/${var.project_name}", "ProcessingTime", "Environment", var.environment, { stat = "Average" }],
            ["MedicalOCR/${var.project_name}", "HandwritingConfidence", "Environment", var.environment, { stat = "Average" }],
          ]
          region = var.region
          period = 300
        }
      },
      # -- Application Load Balancer -------------------------------------------
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title = "Load Balancer - Request Volume & Errors"
          view  = "timeSeries"
          stacked = false
          metrics = [
            ["AWS/ApplicationELB", "RequestCount", ".", ".", { stat = "Sum" }],
            ["AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", ".", ".", { stat = "Sum" }],
          ]
          region = var.region
          period = 300
        }
      },
      # -- Celery Worker Queue Length ------------------------------------------
      {
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          title = "Celery - Queue Depth & Task Duration"
          view  = "timeSeries"
          stacked = false
          metrics = [
            ["MedicalOCR/${var.project_name}", "CeleryQueueLength", "Environment", var.environment, { stat = "Average" }],
            ["MedicalOCR/${var.project_name}", "TaskDuration", "Environment", var.environment, { stat = "Average" }],
          ]
          region = var.region
          period = 300
        }
      },
      # -- EKS Node Group Metrics ----------------------------------------------
      {
        type   = "metric"
        x      = 0
        y      = 12
        width  = 24
        height = 6
        properties = {
          title = "EKS - Node Resource Utilization"
          view  = "timeSeries"
          stacked = false
          metrics = [
            ["AWS/EKS", "node_cpu_utilization", "ClusterName", "${var.project_name}-${var.environment}", { stat = "Average" }],
            ["AWS/EKS", "node_memory_utilization", "ClusterName", "${var.project_name}-${var.environment}", { stat = "Average" }],
          ]
          region = var.region
          period = 300
        }
      },
    ]
  })
}

# =============================================================================
# GLOBAL SERVICES — Route53 DNS Failover & Global Resources
# =============================================================================

# -----------------------------------------------------------------------------
# Route53 Hosted Zone (created if domain_name is provided)
# -----------------------------------------------------------------------------

resource "aws_route53_zone" "medical_ocr" {
  provider = aws.primary
  count    = var.domain_name != "" ? 1 : 0
  name     = var.domain_name

  tags = { Name = "medical-ocr-${var.environment}" }
}

# DNS validation records for ACM certificates
resource "aws_route53_record" "primary_cert_validation" {
  provider = aws.primary
  for_each = var.domain_name != "" ? aws_acm_certificate.primary[0].domain_validation_options : {}
  name     = each.value.resource_record_name
  type     = each.value.resource_record_type
  zone_id  = aws_route53_zone.medical_ocr[0].zone_id
  records  = [each.value.resource_record_value]
  ttl      = 60
}

resource "aws_acm_certificate_validation" "primary" {
  provider                = aws.primary
  count                   = var.domain_name != "" ? 1 : 0
  certificate_arn         = aws_acm_certificate.primary[0].arn
  validation_record_fqdns = [for r in aws_route53_record.primary_cert_validation : r.fqdn]
}

resource "aws_route53_record" "secondary_cert_validation" {
  provider = aws.secondary
  for_each = var.domain_name != "" ? aws_acm_certificate.secondary[0].domain_validation_options : {}
  name     = each.value.resource_record_name
  type     = each.value.resource_record_type
  zone_id  = aws_route53_zone.medical_ocr[0].zone_id
  records  = [each.value.resource_record_value]
  ttl      = 60
}

resource "aws_acm_certificate_validation" "secondary" {
  provider                = aws.secondary
  count                   = var.domain_name != "" ? 1 : 0
  certificate_arn         = aws_acm_certificate.secondary[0].arn
  validation_record_fqdns = [for r in aws_route53_record.secondary_cert_validation : r.fqdn]
}

# -----------------------------------------------------------------------------
# Primary DNS Record (with health check)
# -----------------------------------------------------------------------------

resource "aws_route53_health_check" "primary" {
  provider = aws.primary
  count    = var.domain_name != "" ? 1 : 0

  fqdn              = aws_lb.primary.dns_name
  port              = 443
  type              = "HTTPS"
  resource_path     = "/health"
  request_interval  = 30
  failure_threshold  = 3

  tags = { Name = "medical-ocr-${var.environment}-primary-hc" }
}

resource "aws_route53_record" "primary_a" {
  provider = aws.primary
  count    = var.domain_name != "" ? 1 : 0
  zone_id  = aws_route53_zone.medical_ocr[0].zone_id
  name     = var.domain_name
  type     = "A"

  alias {
    name                   = aws_lb.primary.dns_name
    zone_id                = aws_lb.primary.zone_id
    evaluate_target_health = true
  }

  health_check_id = aws_route53_health_check.primary[0].id

  set_identifier = "primary"
  latency_routing_policy {}
}

# -----------------------------------------------------------------------------
# Secondary DNS Record (failover)
# -----------------------------------------------------------------------------

resource "aws_route53_record" "secondary_a" {
  provider = aws.primary
  count    = var.domain_name != "" ? 1 : 0
  zone_id  = aws_route53_zone.medical_ocr[0].zone_id
  name     = var.domain_name
  type     = "A"

  alias {
    name                   = aws_lb.secondary.dns_name
    zone_id                = aws_lb.secondary.zone_id
    evaluate_target_health = true
  }

  set_identifier = "secondary"
  latency_routing_policy {}
}

# -----------------------------------------------------------------------------
# Failover Routing Policy
# -----------------------------------------------------------------------------

resource "aws_route53_record" "failover" {
  provider = aws.primary
  count    = var.domain_name != "" ? 1 : 0
  zone_id  = aws_route53_zone.medical_ocr[0].zone_id
  name     = var.domain_name
  type     = "A"

  failover_routing_policy {
    type            = "PRIMARY"
    health_check_id = aws_route53_health_check.primary[0].id
  }

  alias {
    name                   = aws_lb.primary.dns_name
    zone_id                = aws_lb.primary.zone_id
    evaluate_target_health = true
  }

  depends_on = [aws_route53_health_check.primary]
}

resource "aws_route53_record" "failover_secondary" {
  provider = aws.primary
  count    = var.domain_name != "" ? 1 : 0
  zone_id  = aws_route53_zone.medical_ocr[0].zone_id
  name     = var.domain_name
  type     = "A"

  failover_routing_policy {
    type = "SECONDARY"
  }

  alias {
    name                   = aws_lb.secondary.dns_name
    zone_id                = aws_lb.secondary.zone_id
    evaluate_target_health = true
  }
}

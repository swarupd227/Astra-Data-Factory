# One warehouse per processing tier. Sizes come from var.warehouse_tiers so
# each environment sets them in its tfvars file and nothing else changes.

resource "snowflake_warehouse" "tier" {
  for_each = var.warehouse_tiers

  name           = local.warehouse_names[each.key]
  comment        = coalesce(each.value.comment, "Astra Data Factory ${var.environment}: ${each.key} tier. Managed by Terraform.")
  warehouse_type = "STANDARD"
  warehouse_size = each.value.size

  auto_suspend        = each.value.auto_suspend_seconds
  auto_resume         = "true"
  initially_suspended = true

  min_cluster_count = 1
  max_cluster_count = each.value.max_cluster_count
  scaling_policy    = "STANDARD"

  statement_timeout_in_seconds        = each.value.statement_timeout_seconds
  statement_queued_timeout_in_seconds = each.value.statement_timeout_seconds

  enable_query_acceleration = "false"

  resource_monitor = one(snowflake_resource_monitor.this[*].name)
}

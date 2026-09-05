output "state_bucket_name" {
  description = "Bucket to put in every environments/backend-<env>.hcl of the foundation."
  value       = module.state_bucket.name
}

output "backend_config_example" {
  description = "Backend file contents for an environment, with this bucket filled in."
  value       = <<-EOT
    bucket       = "${module.state_bucket.name}"
    key          = "foundation/<env>/terraform.tfstate"
    region       = "${var.aws_region}"
    encrypt      = true
    use_lockfile = true
  EOT
}

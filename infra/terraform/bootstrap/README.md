# Bootstrap

Creates the S3 bucket that holds Terraform state for every environment in one AWS account. Run once per account, before the first environment. Part of story S1.2.1.

```bash
cd infra/terraform/bootstrap
terraform init
terraform apply
terraform output state_bucket_name
```

Put the bucket name in each `infra/terraform/foundation/environments/backend-<env>.hcl`. The output `backend_config_example` prints the full file with the name filled in.

State for this root is local by design: it holds one bucket whose name is derived from the account id. `*.tfstate` is git-ignored. If the local state is lost, import the bucket rather than recreating it:

```bash
terraform import module.state_bucket.aws_s3_bucket.this <bucket name>
```

The bucket is versioned, encrypted, private and TLS-only through the same module the foundation uses. Superseded state versions are kept for 90 days.

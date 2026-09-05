output "name" {
  description = "Bucket name."
  value       = aws_s3_bucket.this.bucket
}

output "id" {
  description = "Bucket id (same as the name)."
  value       = aws_s3_bucket.this.id
}

output "arn" {
  description = "Bucket ARN."
  value       = aws_s3_bucket.this.arn
}

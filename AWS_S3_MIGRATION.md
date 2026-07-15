# Migrating Recordings to AWS S3

## Credentials You Will Need

| Variable | What It Is |
|---|---|
| `AWS_ACCESS_KEY_ID` | IAM user access key ID |
| `AWS_SECRET_ACCESS_KEY` | IAM user secret access key |
| `AWS_REGION` | Region where your bucket lives (e.g. `ap-south-1`) |
| `AWS_S3_BUCKET_NAME` | Name of your S3 bucket |

---

## Step 1 — Create an S3 Bucket

1. Go to **AWS Console → S3 → Create bucket**
2. **Bucket name**: e.g. `careerpilot-recordings` (must be globally unique)
3. **Region**: choose the region closest to your users
   - Pakistan / South Asia → **Asia Pacific (Mumbai)** = `ap-south-1`
   - US → `us-east-1`
4. **Block all public access**: leave this **ON** — recordings are private and accessed only via presigned URLs
5. Click **Create bucket**

---

## Step 2 — Create an IAM User

1. Go to **AWS Console → IAM → Users → Create user**
2. **User name**: e.g. `careerpilot-bot`
3. On the permissions page, choose **Attach policies directly → Create policy**
4. Open the **JSON** tab and paste:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject",
        "s3:DeleteObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::careerpilot-recordings",
        "arn:aws:s3:::careerpilot-recordings/*"
      ]
    }
  ]
}
```

> Replace `careerpilot-recordings` with your actual bucket name.

5. Save the policy (e.g. name it `CareerPilotS3Policy`) and attach it to the user
6. On the final screen, **copy or download the Access Key ID and Secret Access Key** — the secret is shown only once

---

## Step 3 — Add CORS to the Bucket

Required so the browser can stream audio through presigned URLs.

1. Go to your bucket → **Permissions → Cross-origin resource sharing (CORS)**
2. Paste:

```json
[
  {
    "AllowedHeaders": ["*"],
    "AllowedMethods": ["GET", "HEAD"],
    "AllowedOrigins": ["*"],
    "ExposeHeaders": ["Content-Length", "Content-Type"],
    "MaxAgeSeconds": 3600
  }
]
```

> In production replace `"*"` in `AllowedOrigins` with your actual frontend domain.

---

## Step 4 — Update Your `.env` File

Remove the old R2 variables and add these four:

```env
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=ap-south-1
AWS_S3_BUCKET_NAME=careerpilot-recordings
```

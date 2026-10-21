# Deploying InsightfulCell on AWS

One EC2 instance runs everything with Docker Compose. A shared Caddy proxy serves HTTPS for
`app.ic.rgproject.me` and `api.ic.rgproject.me`, and can serve other projects under
`*.project2.rgproject.me` later. Uploads go to S3, and nightly database backups go to a second
bucket.

```
browser ──https──▶ Caddy (proxy, :443) ──edge network──▶ ic-web :3000   (Next.js)
   │                                               └──▶ ic-api :8000   (FastAPI, event streams)
   │                                                     worker  ──┐  (private network)
   └──presigned PUT──▶ S3 upload bucket ◀── instance role ──────────┤
                                                         postgres ◀─┘  (private, no ports)
```

| File | What it is |
|---|---|
| `compose.yml` | postgres, api, worker and web, plus the one-off `migrate` and `auth-migrate` steps |
| `deploy.sh` | Builds, creates the database roles, migrates and starts everything. Run it on every update |
| `init-env.sh` | First-time copy of the `*.example` files, with fresh random secrets |
| `backup.sh` | `pg_dump` to S3, run from cron |
| `proxy/` | The shared Caddy proxy for every project on the host. Copy it to `~/proxy` |
| `iam-policy.json`, `s3-cors.json` | The instance role's permissions and the upload bucket's CORS rule |

**Cost:** about 30–40 USD a month, or less with a savings plan. That covers a `t4g.medium`
(~25 USD), 30 GB of gp3 (~3 USD), a public IPv4 address (~3.6 USD) and S3 (cents).

**Why PostgreSQL runs in a container, not RDS:** the first migration creates a `BYPASSRLS`
role (`ic_maintenance`), and that needs a real superuser. RDS's master user is not one, so
`alembic upgrade head` may fail there. Moving to RDS later is possible, but test the migrations
against it first.

Replace these placeholders throughout:

| Placeholder | Example |
|---|---|
| region | `ap-southeast-2` (Sydney). Pick one near your users and use it everywhere |
| `ic-uploads-CHANGE-ME` | `rgproject-ic-uploads` (globally unique, **no dots**) |
| `ic-backups-CHANGE-ME` | `rgproject-backups` |

---

## Step 1: Protect the account

1. Sign in as the root user, turn on **MFA** for root, then create an IAM Identity Center user
   (or an IAM user with `AdministratorAccess`) and work as that user from here on.
2. **Billing → Budgets → Create budget → Monthly cost budget**, e.g. 50 USD, with an email alert.
3. In the top-right corner, select your **region**. Everything below goes in that region.

## Step 2: S3 buckets

**S3 → Create bucket**, twice:

1. **`ic-uploads-CHANGE-ME`**: keep *Block all public access* **on**, plus the default
   encryption. Then **Permissions → Cross-origin resource sharing (CORS) → Edit** and paste
   `s3-cors.json`. Browsers upload straight to this bucket, and the uploader needs the `ETag`
   header.
2. **`ic-backups-CHANGE-ME`**: block public access on. Under **Management → Create lifecycle
   rule**, apply it to all objects and expire current versions after 30 days.

## Step 3: IAM role for the server

1. **IAM → Policies → Create policy → JSON**: paste `iam-policy.json`, replace both bucket
   names, and name it `ic-server`.
2. **IAM → Roles → Create role → AWS service → EC2**: attach `ic-server` and name the role
   `ic-ec2`. The containers get S3 access from this role, so there are no keys in env files.

## Step 4: Launch the EC2 instance

**EC2 → Launch instance**:

| Setting | Value |
|---|---|
| Name | `rgproject-1` |
| AMI | **Ubuntu Server 24.04 LTS**, architecture **64-bit (Arm)** |
| Type | **t4g.medium** (2 vCPU, 4 GB). Analyses use memory, so don't go smaller |
| Key pair | Create one (`rgproject.pem`) and keep it safe |
| Network | Default VPC, public subnet, auto-assign public IP |
| Security group | New, `rgproject-web`: **SSH 22 from My IP**, **HTTP 80** and **HTTPS 443** from anywhere (add **Custom UDP 443** from anywhere for HTTP/3) |
| Storage | **30 GiB gp3** |
| Advanced → IAM instance profile | **`ic-ec2`** |
| Advanced → Metadata version | **V2 only** |
| Advanced → Metadata response hop limit | **2** (containers sit one network hop away; with 1 they get no S3 credentials) |

Then **EC2 → Elastic IPs → Allocate → Associate** it with the instance. The address then stays
the same across reboots.

## Step 5: DNS at Namecheap

**Domain List → `rgproject.me` → Manage → Advanced DNS → Add new record**:

| Type | Host | Value | TTL |
|---|---|---|---|
| A Record | `*.ic` | `<Elastic IP>` | Automatic |

Check it from your Mac. Both should print the Elastic IP:
```bash
dig +short app.ic.rgproject.me
dig +short api.ic.rgproject.me
```

## Step 6: Prepare the server

```bash
chmod 400 ~/Downloads/rgproject.pem
ssh -i ~/Downloads/rgproject.pem ubuntu@<Elastic IP>
```
On the server:
```bash
# Docker Engine and the Compose plugin (docs.docker.com/engine/install/ubuntu)
sudo apt-get update && sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt-get update && sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker ubuntu

# 4 GB swap: building the images can briefly use more memory than the instance has
sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# AWS CLI (for backups) and automatic security updates
sudo snap install aws-cli --classic
sudo apt-get install -y unattended-upgrades

exit   # log out and back in so the docker group applies
```
Log back in and check that the role works. This should print the `ic-ec2` role:
```bash
aws sts get-caller-identity
```

## Step 7: Get the code

The repository is public, so cloning over HTTPS needs no key or token:
```bash
git clone https://github.com/rupeshghimire7/InsightfullCell.git ~/InsightfullCell
```
The server only ever pulls; it never pushes. Because the repository is public, real values
live only in the git-ignored `.env`, `backend.env` and `web.env` on the server (step 9). Never
commit them, and never put a real key in a `*.example` file.

## Step 8: Start the shared proxy

This is done once per server, not once per project:
```bash
docker network create edge
cp -r ~/InsightfullCell/deploy/aws/proxy ~/proxy
nano ~/proxy/Caddyfile        # put your email in the first block
cd ~/proxy && docker compose up -d
```
Until the app is running, Caddy answers with an error. That is expected. Certificates are issued
on the first request once DNS (step 5) resolves.

## Step 9: Configure

```bash
cd ~/InsightfullCell/deploy/aws
./init-env.sh                 # creates .env, backend.env, web.env with random secrets
nano .env                     # BACKUP_BUCKET=ic-backups-CHANGE-ME
nano backend.env              # S3_BUCKET, S3_REGION, LLM_* lines and keys from your backend/.env
nano web.env                  # S3_PUBLIC_ORIGIN (same bucket and region)
```
Save the admin dashboard key that `init-env.sh` printed. The secrets exist only in these three
files on the server (`chmod 600`, ignored by git).

## Step 10: Deploy

```bash
./deploy.sh
```
The first run builds both images, which takes about 10 minutes on a `t4g.medium`. Then it
creates the database roles, runs the API migrations and the sign-in table migration, and
starts `api`, `worker` and `web`. It ends with `docker compose ps`, which shows all three as
running, with api and web `healthy`.

## Step 11: Check it works

1. Open **https://app.ic.rgproject.me**. You should see the padlock and the sign-in page.
2. Register, then upload a file from `sample_data/`. Open the browser dev tools. The **Network**
   tab should show `PUT`s to the S3 bucket succeeding, and live progress coming from
   `api.ic.rgproject.me`.
3. Logs, if something is off:
   ```bash
   docker compose logs -f api worker web     # in deploy/aws
   docker compose -f ~/proxy/compose.yml logs -f caddy
   ```

## Step 12: Optional extras

- **Email sign-in links:** add the domain `ic.rgproject.me` at resend.com and copy the DNS
  records it shows into Namecheap (Advanced DNS; hosts there are relative, so `send.ic`, not
  `send.ic.rgproject.me`). Then set `EMAIL_LINK_SIGN_IN=on`, `EMAIL_TRANSPORT=resend`,
  `RESEND_API_KEY` and `EMAIL_FROM` in `web.env`, and run `docker compose up -d web`.
- **Google sign-in:** create an OAuth client with redirect URI
  `https://app.ic.rgproject.me/api/auth/callback/google`, set both values in `web.env`, and run
  `docker compose up -d web`.

## Step 13: Nightly backups

```bash
./backup.sh                   # try it once, then check that the file is in the backups bucket
crontab -e
# add:
15 3 * * * /home/ubuntu/InsightfullCell/deploy/aws/backup.sh >> /home/ubuntu/ic-backup.log 2>&1
```
Also turn on **EC2 → Elastic Block Store → Lifecycle Manager**: a daily snapshot of the
instance's volume, keeping 7. This covers everything on the server, Caddy's certificates
included.

---

## Everyday operations

| Task | Command (in `~/InsightfullCell/deploy/aws`) |
|---|---|
| Deploy an update | `git pull && ./deploy.sh` |
| Change a setting | edit `backend.env` / `web.env`, then `docker compose up -d` |
| Restart one part | `docker compose restart worker` |
| Status / logs | `docker compose ps`, `docker compose logs -f --tail 100 api` |
| Database shell | `docker compose exec postgres psql -U ic_owner -d insightfulcell` |
| Disk space | `df -h`, `docker system df`; `docker builder prune` frees build cache |

### Restoring a backup

```bash
aws s3 cp s3://ic-backups-CHANGE-ME/ic/insightfulcell-<stamp>.dump /tmp/restore.dump
docker compose stop api worker web
docker compose exec -T postgres pg_restore -U ic_owner -d insightfulcell --clean --if-exists < /tmp/restore.dump
./deploy.sh
```
A dump doesn't contain roles. `deploy.sh` recreates them from `.env`, so keep that file, or the
same passwords, with your backups.

## Adding another project (`*.project2.rgproject.me`)

1. In Namecheap, add an A record with host `*.project2` and the same Elastic IP (or another
   server).
2. In that project's compose file, give the public containers unique names (`p2-web`) and put
   them on both networks:
   ```yaml
   services:
     web:
       container_name: p2-web
       networks: [default, edge]
   networks:
     edge: { external: true }
   ```
   Don't publish ports 80 or 443 there, because the proxy owns them.
3. Add a block to `~/proxy/Caddyfile`, then run
   `docker compose -f ~/proxy/compose.yml exec caddy caddy reload --config /etc/caddy/Caddyfile`.
4. Keep projects apart: give each its own database and passwords, buckets and secrets. Never set
   cookies on `.rgproject.me`, and never list another project's origin in
   `AUTH_TRUSTED_ORIGINS`. All the subdomains count as the *same site* to browsers.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Browser: certificate error | DNS not resolving to the Elastic IP yet, or port 80 closed (Let's Encrypt validates over 80). See the `caddy` logs |
| `api` exits: "must be https in production" | A URL in `backend.env` is still `http://` |
| `api` exits: "S3_ENDPOINT_URL needs S3_PUBLIC_ENDPOINT_URL" | `S3_ENDPOINT_URL=` is in `backend.env`. Remove the whole line |
| Upload fails, browser console says CORS | Bucket CORS missing, or `AllowedOrigins` doesn't match `https://app.ic.rgproject.me` exactly |
| Upload blocked by Content-Security-Policy | `S3_PUBLIC_ORIGIN` doesn't match the host in the presigned URL (compare it in the Network tab) |
| Worker log: `NoCredentialsError` / storage unavailable | Instance role not attached, or metadata hop limit still 1 (EC2 → Actions → Instance settings → Modify instance metadata options) |
| Analyses stay "queued" | The worker isn't running: `docker compose ps worker`, `docker compose logs worker` |
| Sign-in: "403 Invalid origin" | The site was opened at an address other than `BETTER_AUTH_URL` |
| Build killed / exit 137 | Out of memory. Check that swap is on (`swapon --show`) |

# Deploying the public RepoScout app on AWS

> Hosting on Render's free plan instead? See [DEPLOY-RENDER.md](DEPLOY-RENDER.md).

This puts RepoScout on the internet so anyone can paste a public GitHub repository or pull
request link. Visitors use **their own** free Groq API key; it stays in their browser and is
sent only with their own requests, so your API quota is never used.

```
visitor's browser ──HTTPS──> Caddy (auto HTTPS) ──> RepoScout app ──> Ollama (embeddings)
                                                         │
                                                         ├──> GitHub (clone, PR diffs)
                                                         └──> Groq (with the visitor's key)
```

Everything runs from one `docker compose` file on one EC2 instance.

## 1. Pick an instance size (and know what it costs)

On AWS's newer free plan (accounts created on or after 15 July 2025), EC2 usage is paid from
your free credits ($100, plus up to $100 more for completing activities). The plan ends after
**6 months or when the credits run out**, whichever is first. Approximate costs in us-east-1:

| Instance | RAM | Instance cost | Total with disk and IP | Credits last ($200) | Good for |
|---|---|---|---|---|---|
| **t3.small** (recommended) | 2 GB | ~$15/month | ~$21/month | the full 6 months | personal use, demos, small/medium repos |
| m7i-flex.large | 8 GB | ~$70/month | ~$76/month | ~2.5 months | faster indexing, bigger repos |

"Disk and IP" is a 20 GB gp3 volume (~$1.60/month) plus the public IPv4 address (~$3.60/month).
Prices vary a little by region (Mumbai is about 5% more). Check **Billing → Credits** in the
console for your real balance.

To keep the site running after the free plan ends, you need to upgrade the account to a paid
plan, after which the costs above are charged to your card. Until then, you can stop the
instance whenever you're not showing the app to anyone: a stopped instance doesn't use compute
credits, though the disk and the Elastic IP still cost a little.

## 2. Set a spending alarm first

AWS console → **Billing and Cost Management → Budgets → Create budget → Use a template →
Zero spend budget**. You'll get an email if anything starts costing real money.

## 3. Get a free domain (DuckDNS)

HTTPS needs a domain name. DuckDNS gives free subdomains:

1. Go to **duckdns.org** and sign in (GitHub or Google).
2. Add a subdomain, for example `reposcout-yourname`. You'll get `reposcout-yourname.duckdns.org`.
3. Leave the IP for now; you'll fill it in after step 4.

## 4. Launch the instance

EC2 console → **Launch instance**:

- **Name:** `reposcout`
- **AMI:** Ubuntu Server 24.04 LTS
- **Instance type:** `t3.small` (or `m7i-flex.large`, see step 1)
- **Key pair:** create one (`.pem`) and keep the file safe
- **Network settings → Edit → Security group rules:**
  - SSH, port 22, source **My IP**
  - HTTP, port 80, source **Anywhere** (needed for the HTTPS certificate)
  - HTTPS, port 443, source **Anywhere**
- **Storage:** 20 GB gp3

Then give it a fixed address: **EC2 → Elastic IPs → Allocate → Associate** with the instance.
Copy that IP into your DuckDNS subdomain and click **update ip**.

## 5. Install Docker on the server

```bash
chmod 400 ~/Downloads/reposcout.pem
ssh -i ~/Downloads/reposcout.pem ubuntu@YOUR_ELASTIC_IP
```

On the server:

```bash
# 2 GB of swap so a small instance doesn't run out of memory while indexing
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# Docker
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu
exit
```

SSH in again so the `docker` group takes effect.

## 6. Configure and start RepoScout

```bash
git clone https://github.com/YOUR_GITHUB_NAME/reposcout.git
cd reposcout
cp deploy/public.env.example deploy/public.env
nano deploy/public.env
```

In `deploy/public.env`:

- set `DOMAIN=reposcout-yourname.duckdns.org`
- optional but recommended: set `GITHUB_TOKEN` to a fine-grained GitHub token with only
  **Public repositories (read-only)** access. Without it, GitHub allows the server only 60 API
  requests per hour, which runs out quickly once people review pull requests.
- on a **t3.small**, lower the limits: `REPOSCOUT_MAX_CHUNKS=6000` and `REPOSCOUT_MAX_REPOS=15`

`deploy/public.env` is in `.gitignore`, so the token is never committed.

Start everything:

```bash
docker compose -f deploy/docker-compose.public.yml up -d --build
docker compose -f deploy/docker-compose.public.yml logs -f
```

The first start downloads the embedding model (~270 MB) and gets an HTTPS certificate; give it
a minute or two. Press Ctrl+C to stop following the logs (the app keeps running).

Open **https://reposcout-yourname.duckdns.org**. Paste a small repository first, such as
`https://github.com/pallets/itsdangerous`, add your Groq key in the top-right corner, and ask
a question.

## 7. Everyday operations

| Task | Command (run inside `~/reposcout`) |
|---|---|
| See if everything is running | `docker compose -f deploy/docker-compose.public.yml ps` |
| Follow the logs | `docker compose -f deploy/docker-compose.public.yml logs -f reposcout` |
| Deploy a new version | `git pull && docker compose -f deploy/docker-compose.public.yml up -d --build` |
| Restart the app | `docker compose -f deploy/docker-compose.public.yml restart reposcout` |
| Stop everything | `docker compose -f deploy/docker-compose.public.yml down` (indexes are kept) |
| Check disk space | `df -h /` |
| Check memory | `free -h` |

Indexes live in a Docker volume, so they survive restarts and new deployments. Jobs that were
running during a restart are started again automatically.

## 8. How it stays safe and healthy

- **API keys:** visitors' keys are sent over HTTPS in a request header, used for that one
  request, and never written to disk or logs. The server only sends them to the fixed
  provider URL in `public.env`, never to an address a visitor chooses.
- **Cloning:** only `https://github.com/owner/repo` links are accepted. Clones are shallow,
  skip Git LFS files, never prompt for passwords, and have symbolic links disabled, so a
  repository can't trick the server into reading its own files. Git first downloads only the
  list of files and their sizes; oversized repositories are refused before any code is written
  to disk, and only ordinary files under 400 KB are checked out. A clone that runs too long or
  grows too big is killed. Checkouts are deleted after indexing.
- **Size limits:** repositories over `MAX_REPO_MB`, `MAX_FILES` or `MAX_CHUNKS`, indexing jobs
  over `INDEX_TIMEOUT_MINUTES`, and pull requests over `MAX_PR_FILES` / `MAX_PR_LINES` (or a
  2 MB diff) are refused with a clear message. Request bodies over 64 KB are refused by Caddy
  and again by the app.
- **Fair use:** each visitor (by IP address; IPv6 by /64 network) can add
  `LIMIT_INDEX_PER_HOUR` repositories per hour, make `LIMIT_LLM_PER_10MIN` questions or
  reviews and `LIMIT_SEARCH_PER_10MIN` searches per 10 minutes, and have one indexing job and
  one question or review running at a time. "Refresh" is ignored for an index under an hour
  old.
- **Busy server:** at most `MAX_CONCURRENT_LLM` questions/reviews and `MAX_CONCURRENT_SEARCH`
  searches run at once, and at most `MAX_QUEUED` repositories wait to be indexed. Anything
  beyond that gets a polite "busy, try again" instead of slowing everyone down.
- **Containers:** the app runs as an unprivileged user with a memory cap
  (`REPOSCOUT_MEM_LIMIT`, default 1500m; raise it to `6g` on an 8 GB instance). Caddy adds
  security headers, including a Content-Security-Policy.
- **Reviews from links:** opening someone else's review link shows a "Start review" button
  first, so a link can't spend a visitor's API quota without their click.
- **Disk:** at most `MAX_REPOS` indexed repositories are kept; the least recently used are
  removed first. Failed entries are forgotten after a day.
- **One process:** the app must run as a single process (the queue and rate limits live in
  memory). The compose file already does this; don't add `--workers`.

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| Browser says the site can't be reached | Check the security group allows ports 80 and 443, and that DuckDNS shows your Elastic IP |
| Certificate errors in `logs caddy` | DNS isn't pointing at the server yet; wait a few minutes after updating DuckDNS, then `restart caddy` |
| "The embedding service is unavailable" | `logs ollama-init` should end with `success`; if not, run `up -d` again |
| Reviews fail with "GitHub's hourly request limit" | Add a `GITHUB_TOKEN` in `public.env`, then `up -d` |
| Indexing is very slow | Normal on t3.small for large repos; lower `MAX_CHUNKS` or use a bigger instance |
| Out of disk | Lower `MAX_REPOS` and restart; old indexes are removed after the next indexing job |
| Visitors often see "The server is busy" | Raise `REPOSCOUT_MAX_CONCURRENT_LLM` (each slot is one waiting thread; up to ~10 is fine), then `up -d` |
| The app restarts while indexing a big repo | It hit the memory cap: lower `MAX_CHUNKS`, or raise `REPOSCOUT_MEM_LIMIT` on a bigger instance |

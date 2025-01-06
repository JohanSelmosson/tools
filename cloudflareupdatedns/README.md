# Cloudflare DNS Updater

A Python script to automatically update Cloudflare DNS records with your current public IPv4 and IPv6 addresses. Perfect for dynamic DNS setups where your IP address changes periodically.

## Features

- Updates A records with current public IPv4 address
- Updates AAAA records with IPv6 address from specified network interface
- Option to add missing AAAA records for existing A records
- Email notifications for changes and errors
- Detailed logging
- Configuration file support
- Dry run mode for testing

## Installation

1. Clone the repository:
```bash
git clone [repository-url]
cd [repository-directory]
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Create configuration directory:
```bash
mkdir -p ~/.cloudflare
```

## Configuration

### Option 1: Configuration File

Create `~/.cloudflare/config` with your Cloudflare credentials:

```json
{
    "email": "your-cloudflare-email@example.com",
    "api_key": "your-cloudflare-api-token"
}
```

### Option 2: Environment Variable

Set your API key as an environment variable:
```bash
export CF_API_KEY="your-cloudflare-api-token"
```

### Option 3: Command Line Arguments

Provide credentials directly via command line arguments (see Usage section).

## Usage

### Basic Usage

Update A records only:
```bash
python3 dnsupdate.py yourdomain.com -e your@email.com
```

Update both A and AAAA records:
```bash
python3 dnsupdate.py yourdomain.com -e your@email.com -i eth0 --add-aaaa
```

### Email Notifications

Send email reports when changes occur:
```bash
python3 dnsupdate.py yourdomain.com -e your@email.com -m recipient@email.com --mail-from sender@domain.com
```

### Automated Updates

Add to crontab (`crontab -e`):
```bash
# Update DNS records daily at 3 AM
0 3 * * * python3 /path/to/dnsupdate.py yourdomain.com -e your@email.com -q --interface eth0 --add-aaaa -m recipient@email.com --mail-from sender@domain.com

# Force weekly report on Sunday at 4 AM
0 4 * * 0 python3 /path/to/dnsupdate.py yourdomain.com -e your@email.com --interface eth0 --add-aaaa -m recipient@email.com --mail-from sender@domain.com -f
```

## Command Line Options

| Option | Description |
|--------|-------------|
| `domain` | Domain name to update (required) |
| `-e, --email` | Cloudflare account email |
| `-c, --config` | Configuration file path (default: ~/.cloudflare/config) |
| `-i, --interface` | Network interface to use for IPv6 address lookup |
| `-d, --dry-run` | Simulate actions without making changes |
| `-a, --add-aaaa` | Add missing AAAA records for existing A records |
| `-q, --quiet` | Quiet mode, show only critical information |
| `-v, --verbose` | Verbose mode, show detailed information |
| `-f, --force-report` | Always generate a status report, even if no changes |
| `-m, --mail-to` | Email address to send reports to |
| `--mail-from` | Email address to send from (default: root@fully.qualified.domain) |

## Email Notifications

The script can send email notifications in the following cases:
- When DNS records are updated
- When errors occur
- Weekly status reports (when using -f flag)

Email reports include:
- Current IPv4 and IPv6 addresses
- List of changes made
- Any errors encountered
- Timestamp of the update

## Logging

All operations are logged to `~/.cloudflare/dns_update.log` with timestamps and log levels.

View logs:
```bash
tail -f ~/.cloudflare/dns_update.log
```

## Examples

### Basic DNS Update

Update A records only:
```bash
python3 dnsupdate.py example.com -e admin@example.com
```

### Full IPv6 Support

Update both A and AAAA records, adding AAAA records where missing:
```bash
python3 dnsupdate.py example.com -e admin@example.com -i eth0 --add-aaaa
```

### Testing Changes

Dry run to see what would change:
```bash
python3 dnsupdate.py example.com -e admin@example.com -d -v
```

### Email Notifications

Send notifications for changes:
```bash
python3 dnsupdate.py example.com -e admin@example.com \
    -m notifications@example.com \
    --mail-from dns-updater@example.com
```

### Production Setup

Quiet mode with email notifications:
```bash
python3 dnsupdate.py example.com \
    -e admin@example.com \
    -i eth0 \
    --add-aaaa \
    -q \
    -m notifications@example.com \
    --mail-from dns-updater@example.com
```

## Troubleshooting

### Email Issues

1. Ensure postfix/sendmail is running:
```bash
systemctl status postfix
```

2. Check mail logs:
```bash
tail -f /var/log/mail.log
```

3. Run in verbose mode to see SMTP details:
```bash
python3 dnsupdate.py example.com -e admin@example.com -v
```

### Common Problems

1. "Failed to connect to local SMTP server"
   - Check if postfix is running
   - Verify port 25 is open: `telnet localhost 25`

2. "API token verification failed"
   - Verify your API token has the necessary permissions
   - Check if the token is valid in Cloudflare dashboard

3. "No permanent global IPv6 address found"
   - Verify the specified interface has a global IPv6 address
   - Check interface status: `ip -6 addr show dev eth0`

## License

This project is licensed under the MIT License - see the LICENSE file for details.

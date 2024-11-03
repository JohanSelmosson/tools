#!/usr/bin/env python3

import argparse
import json
import logging
import os
import sys
from datetime import datetime
import subprocess


import requests
import netifaces

# Constants
CONFIG_FILE = os.path.expanduser("~/.cloudflare/config")
LOG_FILE = os.path.expanduser("~/.cloudflare/dns_update.log")
API_BASE_URL = "https://api.cloudflare.com/client/v4"

class CloudflareDNSUpdater:
    def __init__(self, domain, email, api_key, interface=None, add_aaaa=False, dry_run=False):
        self.domain = domain
        self.email = email
        self.api_key = api_key
        self.interface = interface
        self.add_aaaa = add_aaaa
        self.dry_run = dry_run
        self.zone_id = None
        self.ipv4 = None
        self.ipv6 = None

    def setup_logging(self, verbose, quiet):
        level = logging.INFO
        if verbose:
            level = logging.DEBUG
        elif quiet:
            level = logging.WARNING

        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[
                logging.FileHandler(LOG_FILE),
                logging.StreamHandler()
            ]
        )

    def verify_api_key(self):
        response = self.cf_api_call("/user/tokens/verify")
        if response.get('success') and response.get('result', {}).get('status') == 'active':
            logging.info("API token verified successfully.")
        else:
            logging.error(f"API token verification failed. Response: {response}")
            sys.exit(1)

    def get_public_ip(self):
        try:
            self.ipv4 = requests.get("https://api.ipify.org").text
            logging.info(f"Public IPv4: {self.ipv4}")
        except requests.RequestException:
            logging.error("Failed to retrieve public IPv4 address")
            sys.exit(1)

        if self.interface:
            try:
                output = subprocess.check_output(['ip', '-6', 'addr', 'show', self.interface], universal_newlines=True)
                ipv6_addresses = []
                for line in output.split('\n'):
                    if 'inet6' in line and 'scope global' in line and 'temporary' not in line:
                        addr = line.split()[1].split('/')[0]
                        if not addr.startswith('fe80'):
                            ipv6_addresses.append(addr)
                if ipv6_addresses:
                    self.ipv6 = ipv6_addresses[0]  # Choose the first non-temporary global address
                    logging.info(f"Permanent IPv6 address found on interface {self.interface}: {self.ipv6}")
                else:
                    logging.warning(f"No permanent global IPv6 address found on interface {self.interface}")
            except subprocess.CalledProcessError:
                logging.warning(f"Failed to retrieve IPv6 address for interface {self.interface}")
            except Exception as e:
                logging.warning(f"Error while getting IPv6 address: {e}")
        else:
            logging.warning("No interface specified for IPv6 lookup")

    def cf_api_call(self, endpoint, method="GET", data=None):
        url = f"{API_BASE_URL}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        try:
            response = requests.request(method, url, headers=headers, json=data)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logging.error(f"API call failed: {e}")
            sys.exit(1)

    def get_zone_id(self):
        response = self.cf_api_call(f"/zones?name={self.domain}")
        try:
            self.zone_id = response['result'][0]['id']
            logging.info(f"Retrieved Zone ID: {self.zone_id} for domain {self.domain}")
        except (KeyError, IndexError):
            logging.error(f"Unable to fetch Zone ID for {self.domain}. Check your domain name and API credentials.")
            sys.exit(1)

    def update_dns_record(self, record_type, record_name, old_ip, new_ip, record_id, ttl, proxied):
        if self.dry_run:
            logging.info(f"[DRY RUN] Would update {record_type} record for {record_name} from {old_ip} to {new_ip}")
            return

        logging.info(f"Updating {record_type} record for {record_name}")
        logging.info(f"  Old: {old_ip} -> New: {new_ip}")

        data = {
            "type": record_type,
            "name": record_name,
            "content": new_ip,
            "ttl": ttl,
            "proxied": proxied
        }

        response = self.cf_api_call(f"/zones/{self.zone_id}/dns_records/{record_id}", method="PUT", data=data)
        if not response.get('success'):
            logging.error(f"Failed to update {record_type} record for {record_name}: {response.get('errors')}")

    def add_aaaa_record(self, record_name, ipv6, ttl, proxied):
        if self.dry_run:
            logging.info(f"[DRY RUN] Would add AAAA record for {record_name} with IP {ipv6}")
            return

        logging.info(f"Adding AAAA record for {record_name}")

        data = {
            "type": "AAAA",
            "name": record_name,
            "content": ipv6,
            "ttl": ttl,
            "proxied": proxied
        }

        response = self.cf_api_call(f"/zones/{self.zone_id}/dns_records", method="POST", data=data)
        if not response.get('success'):
            logging.error(f"Failed to add AAAA record for {record_name}: {response.get('errors')}")

    def update_dns(self):
        a_records = self.cf_api_call(f"/zones/{self.zone_id}/dns_records?type=A")
        aaaa_records = self.cf_api_call(f"/zones/{self.zone_id}/dns_records?type=AAAA")

        logging.info("Checking DNS records...")
        logging.info(f"{len(a_records['result'])} A records and {len(aaaa_records['result'])} AAAA records found.")

        # Update A records
        for record in a_records['result']:
            if record['content'] != self.ipv4:
                self.update_dns_record("A", record['name'], record['content'], self.ipv4, record['id'], record['ttl'], record['proxied'])

        # Update AAAA records
        if self.ipv6:
            for record in aaaa_records['result']:
                if record['content'] != self.ipv6:
                    self.update_dns_record("AAAA", record['name'], record['content'], self.ipv6, record['id'], record['ttl'], record['proxied'])

        # Add missing AAAA records if the option is enabled
        if self.add_aaaa and self.ipv6:
            existing_aaaa = {record['name'] for record in aaaa_records['result']}
            for record in a_records['result']:
                if record['name'] not in existing_aaaa:
                    self.add_aaaa_record(record['name'], self.ipv6, record['ttl'], record['proxied'])

        logging.info("DNS update completed successfully.")

def main():
    parser = argparse.ArgumentParser(description="Update Cloudflare DNS records")
    parser.add_argument("domain", help="Domain name to update")
    parser.add_argument("-e", "--email", help="Cloudflare account email")
    parser.add_argument("-c", "--config", default=CONFIG_FILE, help="Configuration file path")
    parser.add_argument("-i", "--interface", help="Network interface to use for IPv6 address lookup")
    parser.add_argument("-d", "--dry-run", action="store_true", help="Simulate actions without making changes")
    parser.add_argument("-a", "--add-aaaa", action="store_true", help="Add missing AAAA records")
    parser.add_argument("-q", "--quiet", action="store_true", help="Quiet mode, show only critical information")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose mode, show detailed information")
    args = parser.parse_args()

    # Setup basic logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    config = {}
    if os.path.exists(args.config):
        try:
            with open(args.config, 'r') as f:
                config = json.load(f)
        except json.JSONDecodeError:
            logging.warning(f"Config file {args.config} is not valid JSON. Ignoring it.")
        except Exception as e:
            logging.warning(f"Error reading config file {args.config}: {e}")

    email = args.email or config.get('email')
    api_key = os.environ.get('CF_API_KEY') or config.get('api_key')

    if not email or not api_key:
        logging.error("Cloudflare email and API key are required. Provide them via arguments, environment variables, or in the config file.")
        sys.exit(1)

    updater = CloudflareDNSUpdater(args.domain, email, api_key, args.interface, args.add_aaaa, args.dry_run)
    updater.setup_logging(args.verbose, args.quiet)
    updater.verify_api_key()
    updater.get_public_ip()
    updater.get_zone_id()
    updater.update_dns()

if __name__ == "__main__":
    main()


#!/usr/bin/env python3

"""
Cloudflare DNS Updater

This script updates Cloudflare DNS records with the current public IPv4 address
and optionally IPv6 address. It can update existing A and AAAA records and
optionally add new AAAA records for domains that only have A records.
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
import subprocess
from typing import Optional, Dict, Any, List
import io

import requests
import netifaces

# Constants
CONFIG_DIR = os.path.expanduser("~/.cloudflare")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config")
LOG_FILE = os.path.join(CONFIG_DIR, "dns_update.log")
API_BASE_URL = "https://api.cloudflare.com/client/v4"

# Create a custom handler that captures SMTP debug output
class SMTPDebugHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.stream = io.StringIO()

    def emit(self, record):
        msg = self.format(record)
        self.stream.write(msg + '\n')

    def get_output(self):
        return self.stream.getvalue()

class CloudflareDNSUpdater:
    """A class to handle updating Cloudflare DNS records."""
    def __init__(self, domain: str, email: str, api_key: str, interface: Optional[str] = None, 
                 add_aaaa: bool = False, dry_run: bool = False, force_report: bool = False) -> None:
        """
        Initialize the CloudflareDNSUpdater.

        Args:
            domain: The domain name to update
            email: Cloudflare account email
            api_key: Cloudflare API key
            interface: Network interface to use for IPv6 address lookup
            add_aaaa: Whether to add missing AAAA records
            dry_run: Whether to simulate actions without making changes
        """
        self.domain = domain
        self.email = email
        self.api_key = api_key
        self.interface = interface
        self.add_aaaa = add_aaaa
        self.dry_run = dry_run
        self.force_report = force_report
        self.zone_id = None
        self.ipv4 = None
        self.ipv6 = None
        self.changes: List[str] = []
        self.errors: List[str] = []
        self.run_start_time = datetime.now()
        self.smtp_debug = SMTPDebugHandler()

    def setup_logging(self, verbose: bool, quiet: bool) -> None:
        """
        Set up logging configuration.

        Args:
            verbose: Enable verbose logging
            quiet: Enable quiet mode (only critical information)
        """
        # Ensure log directory exists
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        level = logging.INFO
        if verbose:
            level = logging.DEBUG
        elif quiet:
            level = logging.WARNING

        # Set up handlers
        file_handler = logging.FileHandler(LOG_FILE)
        file_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
        
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter('%(message)s'))
        
        # Configure root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(level)
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)
        root_logger.addHandler(self.smtp_debug)

    def add_error(self, error: str) -> None:
        """Add an error message."""
        self.errors.append(error)
        logging.error(error)

    def verify_api_key(self) -> None:
        """Verify the Cloudflare API key is valid."""
        try:
            response = self.cf_api_call("/user/tokens/verify")
            if response.get('success') and response.get('result', {}).get('status') == 'active':
                logging.info("API token verified successfully.")
            else:
                error = response.get('errors', [{'message': 'Unknown error'}])[0].get('message')
                self.add_error(f"API token verification failed: {error}")
                raise SystemExit(1)
        except requests.RequestException as e:
            self.add_error(f"API token verification failed: {str(e)}")
            raise SystemExit(1)

    def get_public_ip(self) -> None:
        """Retrieve public IPv4 and IPv6 addresses."""
        try:
            self.ipv4 = requests.get("https://api.ipify.org").text
            logging.info(f"Public IPv4: {self.ipv4}")
        except requests.RequestException as e:
            self.add_error(f"Failed to retrieve public IPv4 address: {str(e)}")
            raise SystemExit(1)

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
                    self.add_error(f"No permanent global IPv6 address found on interface {self.interface}")
            except subprocess.CalledProcessError as e:
                self.add_error(f"Failed to retrieve IPv6 address for interface {self.interface}: {str(e)}")
            except Exception as e:
                self.add_error(f"Error while getting IPv6 address: {str(e)}")

    def cf_api_call(self, endpoint: str, method: str = "GET", data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Make a Cloudflare API call.

        Args:
            endpoint: API endpoint to call
            method: HTTP method to use
            data: Request data to send

        Returns:
            API response as dictionary
        """
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
            self.add_error(f"API call failed: {str(e)}")
            raise SystemExit(1)

    def get_zone_id(self) -> None:
        """Retrieve the zone ID for the domain."""
        try:
            response = self.cf_api_call(f"/zones?name={self.domain}")
            self.zone_id = response['result'][0]['id']
            logging.info(f"Retrieved Zone ID: {self.zone_id} for domain {self.domain}")
        except (KeyError, IndexError):
            self.add_error(f"Unable to fetch Zone ID for {self.domain}. Check your domain name and API credentials.")
            raise SystemExit(1)

    def log_change(self, message: str) -> None:
        """Log a change and add it to the changes list."""
        self.changes.append(message)
        logging.info(message)

    def update_dns_record(self, record_type: str, record_name: str, old_ip: str, 
                         new_ip: str, record_id: str, ttl: int, proxied: bool) -> None:
        """Update a DNS record."""
        if self.dry_run:
            self.log_change(f"Would update {record_type} record for {record_name} from {old_ip} to {new_ip}")
            return

        self.log_change(f"Updating {record_type} record for {record_name} from {old_ip} to {new_ip}")

        data = {
            "type": record_type,
            "name": record_name,
            "content": new_ip,
            "ttl": ttl,
            "proxied": proxied
        }

        response = self.cf_api_call(f"/zones/{self.zone_id}/dns_records/{record_id}", method="PUT", data=data)
        if not response.get('success'):
            error = response.get('errors', [{'message': 'Unknown error'}])[0].get('message')
            self.add_error(f"Failed to update {record_type} record for {record_name}: {error}")

    def add_aaaa_record(self, record_name: str, ipv6: str, ttl: int, proxied: bool) -> None:
        """Add a new AAAA record."""
        if self.dry_run:
            self.log_change(f"Would add AAAA record for {record_name} with IP {ipv6}")
            return

        self.log_change(f"Adding AAAA record for {record_name} with IP {ipv6}")

        data = {
            "type": "AAAA",
            "name": record_name,
            "content": ipv6,
            "ttl": ttl,
            "proxied": proxied
        }

        response = self.cf_api_call(f"/zones/{self.zone_id}/dns_records", method="POST", data=data)
        if not response.get('success'):
            error = response.get('errors', [{'message': 'Unknown error'}])[0].get('message')
            self.add_error(f"Failed to add AAAA record for {record_name}: {error}")

    def generate_status_report(self) -> str:
        """Generate a clean status report for email output."""
        report = []
        
        # Add dry run warning if applicable
        if self.dry_run:
            report.append("*** DRY RUN MODE - NO CHANGES WERE MADE ***")
            report.append("")
        
        # Basic information
        report.append(f"DNS Update for {self.domain}")
        report.append(f"Time: {self.run_start_time.strftime('%Y-%m-%d %H:%M')}")
        report.append("")
        
        # Add errors section if there were any errors
        if self.errors:
            report.append("Errors:")
            for error in self.errors:
                report.append(f"• {error}")
            report.append("")
        
        # Add IP information
        report.append("IP Addresses:")
        report.append(f"• IPv4: {self.ipv4 or 'Not available'}")
        if self.interface:
            report.append(f"• IPv6: {self.ipv6 or 'Not found'} (Interface: {self.interface})")
        report.append("")
        
        # Add changes section if there were any changes
        if self.changes:
            report.append("Changes Made:")
            for change in self.changes:
                report.append(f"• {change}")
        elif not self.errors:  # Only show if there were no errors
            report.append("No changes were necessary.")
        
        return "\n".join(report)

    def send_email(self, recipient: str, subject: str, body: str, from_addr: Optional[str] = None) -> None:
        """
        Send an email using the local SMTP server.
        
        Args:
            recipient: Email address to send to
            subject: Email subject
            body: Email body
            from_addr: Email address to send from (optional)
        """
        import smtplib
        from email.message import EmailMessage
        from socket import getfqdn

        try:
            logging.debug("Preparing email message...")
            msg = EmailMessage()
            msg.set_content(body)
            
            # Use provided from_addr or construct default using FQDN
            if not from_addr:
                fqdn = getfqdn()
                from_addr = f"root@{fqdn}"
            
            msg['Subject'] = subject
            msg['From'] = f"Cloudflare DNS Updater <{from_addr}>"
            msg['To'] = recipient
            
            logging.debug(f"Email details:")
            logging.debug(f"From: {from_addr}")
            logging.debug(f"To: {recipient}")
            logging.debug(f"Subject: {subject}")
            logging.debug("Body:")
            logging.debug(body)
            
            logging.debug("Connecting to local SMTP server...")
            with smtplib.SMTP('localhost') as server:
                # Only enable SMTP debug in debug mode
                if logging.getLogger().getEffectiveLevel() == logging.DEBUG:
                    server.set_debuglevel(1)
                    # Capture SMTP debug output
                    self.smtp_debug.stream = io.StringIO()
                
                server.send_message(msg)
                
                # Log SMTP debug output to file if in debug mode
                if logging.getLogger().getEffectiveLevel() == logging.DEBUG:
                    smtp_output = self.smtp_debug.get_output()
                    logging.debug("SMTP Debug Output:")
                    logging.debug(smtp_output)
                
                logging.debug("Email sent successfully")
        except ConnectionRefusedError:
            error = "Failed to connect to local SMTP server. Is it running?"
            logging.error(error)
            self.add_error(error)
        except Exception as e:
            error = f"Error sending email: {str(e)}"
            logging.error(error)
            self.add_error(error)

    def update_dns(self) -> None:
        """Update all DNS records with current IP addresses."""
        try:
            a_records = self.cf_api_call(f"/zones/{self.zone_id}/dns_records?type=A")
            aaaa_records = self.cf_api_call(f"/zones/{self.zone_id}/dns_records?type=AAAA")

            logging.info(f"{len(a_records['result'])} A records and {len(aaaa_records['result'])} AAAA records found.")

            # Update A records
            for record in a_records['result']:
                if record['content'] != self.ipv4:
                    self.update_dns_record("A", record['name'], record['content'], self.ipv4, 
                                         record['id'], record['ttl'], record['proxied'])

            # Update AAAA records
            if self.ipv6:
                for record in aaaa_records['result']:
                    if record['content'] != self.ipv6:
                        self.update_dns_record("AAAA", record['name'], record['content'], self.ipv6,
                                             record['id'], record['ttl'], record['proxied'])

            # Add missing AAAA records if the option is enabled
            if self.add_aaaa and self.ipv6:
                existing_aaaa = {record['name'] for record in aaaa_records['result']}
                for record in a_records['result']:
                    if record['name'] not in existing_aaaa:
                        self.add_aaaa_record(record['name'], self.ipv6, record['ttl'], record['proxied'])

        except Exception as e:
            self.add_error(f"Failed to update DNS records: {str(e)}")
            raise SystemExit(1)

def main() -> None:
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(description="Update Cloudflare DNS records")
    parser.add_argument("domain", help="Domain name to update")
    parser.add_argument("-e", "--email", help="Cloudflare account email")
    parser.add_argument("-c", "--config", default=CONFIG_FILE, help="Configuration file path")
    parser.add_argument("-i", "--interface", help="Network interface to use for IPv6 address lookup")
    parser.add_argument("-d", "--dry-run", action="store_true", help="Simulate actions without making changes")
    parser.add_argument("-a", "--add-aaaa", action="store_true", help="Add missing AAAA records")
    parser.add_argument("-q", "--quiet", action="store_true", help="Quiet mode, show only critical information")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose mode, show detailed information")
    parser.add_argument("-f", "--force-report", action="store_true", help="Always generate a status report, even if no changes were made")
    parser.add_argument("-m", "--mail-to", help="Email address to send reports to")
    parser.add_argument("--mail-from", help="Email address to send from (default: root@fully.qualified.domain)")
    args = parser.parse_args()

    config = {}
    if os.path.exists(args.config):
        try:
            with open(args.config, 'r') as f:
                config = json.load(f)
        except json.JSONDecodeError:
            print(f"Error: Config file {args.config} is not valid JSON")
            sys.exit(1)
        except Exception as e:
            print(f"Error reading config file {args.config}: {e}")
            sys.exit(1)

    email = args.email or config.get('email')
    api_key = os.environ.get('CF_API_KEY') or config.get('api_key')

    if not email or not api_key:
        print("Error: Cloudflare email and API key are required. Provide them via arguments, environment variables, or in the config file.")
        sys.exit(1)

    updater = CloudflareDNSUpdater(args.domain, email, api_key, args.interface, args.add_aaaa, args.dry_run, args.force_report)
    updater.setup_logging(args.verbose, args.quiet)
    
    try:
        updater.verify_api_key()
        updater.get_public_ip()
        updater.get_zone_id()
        updater.update_dns()

        # Send email report if there are changes, errors, or if forced
        if (updater.changes or updater.errors or args.force_report) and args.mail_to:
            subject = f"DNS Update for {args.domain}"
            if updater.errors:
                subject += " [ERROR]"
            elif updater.changes:
                subject += " [UPDATED]"
            updater.send_email(args.mail_to, subject, updater.generate_status_report(), args.mail_from)

    except SystemExit:
        # Send error report via email if configured
        if updater.errors and args.mail_to:
            subject = f"DNS Update for {args.domain} [ERROR]"
            updater.send_email(args.mail_to, subject, updater.generate_status_report(), args.mail_from)
        sys.exit(1)

if __name__ == "__main__":
    main()

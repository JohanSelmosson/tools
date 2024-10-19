#!/bin/bash

# Default values
CONFIG_FILE="$HOME/.cloudflare/config"
IP_FILE="$HOME/.cloudflare/lastknown_ip_addresses.txt"
DRY_RUN=false

show_help() {
    echo "Usage: $0 [OPTIONS] DOMAIN_NAME"
    echo
    echo "Options:"
    echo "  -e, --email EMAIL        Cloudflare account email"
    echo "  -c, --config FILE        Configuration file (default: $CONFIG_FILE)"
    echo "  -f, --ip-file FILE       File to store IP addresses (default: $IP_FILE)"
    echo "  -d, --dry-run            Simulate actions without making changes"
    echo "  -h, --help               Display this help message"
    echo
    echo "Environment variables:"
    echo "  CF_API_KEY               Cloudflare API key (required if not in config file)"
    echo
    echo "Example:"
    echo "  CF_API_KEY=your_api_key $0 -e user@example.com --dry-run example.com"
}

# Parse command-line options
while [[ $# -gt 0 ]]; do
    case $1 in
        -e|--email)
            CF_EMAIL="$2"
            shift 2
            ;;
        -c|--config)
            CONFIG_FILE="$2"
            shift 2
            ;;
        -f|--ip-file)
            IP_FILE="$2"
            shift 2
            ;;
        -d|--dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            break
            ;;
    esac
done

# Check for required argument
if [ $# -ne 1 ]; then
    echo "Error: DOMAIN_NAME is required."
    show_help
    exit 1
fi

DOMAIN_NAME="$1"
ZONE_NAME="$1"
ZONE_ID="$2"

# Load configuration file if it exists
if [ -f "$CONFIG_FILE" ]; then
    while IFS='=' read -r key value
    do
        # Remove any leading/trailing whitespace
        key=$(echo "$key" | xargs)
        value=$(echo "$value" | xargs)

        # Skip empty lines and comments
        if [ -n "$key" ] && [[ ! "$key" =~ ^# ]]; then
            export "$key=$value"
        fi
    done < "$CONFIG_FILE"
fi

# Check for required variables
if [ -z "$email" ]; then
    if [ -z "$CF_EMAIL" ]; then
        echo "Error: Cloudflare email is required. Provide it via -e option or in the config file."
        exit 1
    else
        email="$CF_EMAIL"
    fi
fi

# In the main script
if [ -z "$CF_API_KEY" ]; then
    echo "Error: Cloudflare API key is required. Set it in the CF_API_KEY environment variable or in the config file."
    exit 1
fi

# Only set CF_EMAIL if it's not already set (i.e., from environment variable)
if [ -z "$CF_EMAIL" ]; then
    CF_EMAIL="$email"
fi

# Only set CF_API_KEY if it's not already set (i.e., from environment variable)
if [ -z "$CF_API_KEY" ]; then
    CF_API_KEY="$api_key"
fi

# Function to get public IP addresses
get_public_ip() {
    # Get IPv4 address
    IPV4=$(curl -s https://api.ipify.org)

    # Get non-privacy IPv6 address locally
    IPV6=$(ip -6 addr show scope global | grep -v temporary | grep -oP '(?<=inet6 )[0-9a-f:]+' | head -n 1)

    # If no IPv6 address found, set it to empty string
    if [ -z "$IPV6" ]; then
        IPV6=""
    fi

    echo "IPv4: $IPV4"
    echo "IPv6: $IPV6"
}


verify_api_key() {
    local api_response=$(cf_api_call "/user/tokens/verify")
    #echo "API Response: $api_response" >&2
    local success=$(echo "$api_response" | jq -r '.success')

    if [ "$success" != "true" ]; then
        echo "Error: API token verification failed. Response: $api_response" >&2
        exit 1
    fi

    echo "API token verified successfully."
}

cf_api_call() {
    local endpoint=$1
    local method=${2:-GET}
    local data=${3:-""}

    echo "Calling API endpoint: $endpoint" >&2
    echo "Method: $method" >&2

    curl -s -X "$method" "https://api.cloudflare.com/client/v4$endpoint" \
         -H "Authorization: Bearer $CF_API_KEY" \
         -H "Content-Type: application/json" \
         ${data:+-d "$data"}
}


# Function to get Zone ID
get_zone_id() {
    local domain=$1
    local api_response=$(cf_api_call "/zones?name=$domain")

    local success=$(echo "$api_response" | jq -r '.success')
    if [ "$success" != "true" ]; then
        echo "Error: API request failed. Response: $api_response" >&2
        exit 1
    fi

    local zone_id=$(echo "$api_response" | jq -r '.result[0].id')

    if [ "$zone_id" = "null" ] || [ -z "$zone_id" ]; then
        echo "Error: Unable to fetch Zone ID for $domain. Please check your domain name and API credentials." >&2
        exit 1
    fi

    echo $zone_id
}

# Function to send email
send_email() {
    local message="$1"
    local subject="Cloudflare DNS Update Notification"
    if [ "$DRY_RUN" = true ]; then
        subject="[DRY RUN] $subject"
        message="[DRY RUN] The following changes would be made:\n\n$message"
    fi
    echo -e "$message" | mail -s "$subject" "$CF_EMAIL"
}

# Function to update DNS records
update_cloudflare_dns() {
    local record_type=$1
    local old_ip=$2
    local new_ip=$3

    # Fetch all DNS records of the specified type
    records=$(cf_api_call "/zones/$ZONE_ID/dns_records?type=$record_type")

    # Loop through each record
    echo $records | jq -c '.result[]' | while read -r record; do
        record_id=$(echo $record | jq -r '.id')
        record_name=$(echo $record | jq -r '.name')
        current_content=$(echo $record | jq -r '.content')
        current_proxied=$(echo $record | jq -r '.proxied')
        current_ttl=$(echo $record | jq -r '.ttl')

        if [ "$current_content" == "$old_ip" ]; then
            if [ "$DRY_RUN" = true ]; then
                echo "[DRY RUN] Would update $record_type record for $record_name from $old_ip to $new_ip"
            else
                echo "Updating $record_type record for $record_name from $old_ip to $new_ip"
                update_result=$(cf_api_call "/zones/$ZONE_ID/dns_records/$record_id" "PUT" \
                    "{\"type\":\"$record_type\",\"name\":\"$record_name\",\"content\":\"$new_ip\",\"ttl\":$current_ttl,\"proxied\":$current_proxied}")

                if [ "$(echo $update_result | jq -r '.success')" == "true" ]; then
                    CHANGES+="Updated $record_type record for $record_name from $old_ip to $new_ip\n"
                else
                    error_message=$(echo $update_result | jq -r '.errors[0].message')
                    CHANGES+="Failed to update $record_type record for $record_name: $error_message\n"
                fi
            fi
        fi
    done
}

# Function to add missing AAAA records
add_missing_aaaa_records() {
    # Fetch all A records
    a_records=$(cf_api_call "/zones/$ZONE_ID/dns_records?type=A")

    # Loop through each A record
    echo $a_records | jq -c '.result[]' | while read -r record; do
        record_name=$(echo $record | jq -r '.name')
        record_content=$(echo $record | jq -r '.content')
        record_proxied=$(echo $record | jq -r '.proxied')
        record_ttl=$(echo $record | jq -r '.ttl')

        # Check if this A record points to our current IPv4
        if [ "$record_content" == "$IPV4" ]; then
            # Check if AAAA record exists for this name
            aaaa_check=$(cf_api_call "/zones/$ZONE_ID/dns_records?type=AAAA&name=$record_name")

            if [ "$(echo $aaaa_check | jq -r '.result_info.count')" -eq "0" ]; then
                if [ "$DRY_RUN" = true ]; then
                    echo "[DRY RUN] Would add AAAA record for $record_name with IP $IPV6"
                else
                    echo "Adding AAAA record for $record_name"
                    add_result=$(cf_api_call "/zones/$ZONE_ID/dns_records" "POST" \
                        "{\"type\":\"AAAA\",\"name\":\"$record_name\",\"content\":\"$IPV6\",\"ttl\":$record_ttl,\"proxied\":$record_proxied}")

                    if [ "$(echo $add_result | jq -r '.success')" == "true" ]; then
                        CHANGES+="Added AAAA record for $record_name with IP $IPV6\n"
                    else
                        error_message=$(echo $add_result | jq -r '.errors[0].message')
                        CHANGES+="Failed to add AAAA record for $record_name: $error_message\n"
                    fi
                fi
            fi
        fi
    done
}


# Function to read previous IPs
read_previous_ips() {
    if [ -f "$IP_FILE" ]; then
        PREV_IPV4=$(sed -n '1p' "$IP_FILE")
        PREV_IPV6=$(sed -n '2p' "$IP_FILE")
    else
        PREV_IPV4=""
        PREV_IPV6=""
    fi
}

# Function to save current IPs
save_current_ips() {
    echo "$IPV4" > "$IP_FILE"
    echo "$IPV6" >> "$IP_FILE"
}

# Main script
if [ "$DRY_RUN" = true ]; then
    echo "Running in DRY RUN mode. No changes will be made."
fi

# Verify API token
verify_api_key

# Get Zone ID
ZONE_ID=$(get_zone_id "$1")
if [ $? -ne 0 ]; then
    echo "Failed to retrieve Zone ID. Exiting."
    exit 1
fi
echo "Retrieved Zone ID: $ZONE_ID for domain $1"

# Get Zone ID
ZONE_ID=$(get_zone_id "$DOMAIN_NAME")
echo "Retrieved Zone ID: $ZONE_ID for domain $DOMAIN_NAME"

get_public_ip
read_previous_ips

CHANGES=""

if [ "$IPV4" != "$PREV_IPV4" ]; then
    CHANGES+="IPv4 address changed from $PREV_IPV4 to $IPV4\n"
    update_cloudflare_dns "A" "$PREV_IPV4" "$IPV4"
fi

if [ "$IPV6" != "$PREV_IPV6" ] && [ -n "$IPV6" ]; then
    CHANGES+="IPv6 address changed from $PREV_IPV6 to $IPV6\n"
    update_cloudflare_dns "AAAA" "$PREV_IPV6" "$IPV6"
fi

# Add missing AAAA records
add_missing_aaaa_records

if [ -n "$CHANGES" ]; then
    send_email "$CHANGES"
    if [ "$DRY_RUN" = false ]; then
        save_current_ips
    fi
else
    echo "No IP address changes detected."
fi

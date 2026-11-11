#!/bin/sh
# TLS certificate for nginx (/tmp/tls), run by the image entrypoint before nginx starts.
# - Let's Encrypt certificate present in /certs (written by the certbot service): copied.
# - None yet (first start, local use): temporary self-signed certificate, so nginx can
#   start and answer the HTTP-01 challenge on port 80.
# A background loop then picks up the first issuance and every renewal (nginx reload).
set -eu
TLS=/tmp/tls
mkdir -p "$TLS"

sync_cert() {
    if [ -s /certs/fullchain.pem ] && [ -s /certs/privkey.pem ]; then
        if ! cmp -s /certs/fullchain.pem "$TLS/fullchain.pem"; then
            cp /certs/privkey.pem "$TLS/privkey.pem.new" && cp /certs/fullchain.pem "$TLS/fullchain.pem.new"
            mv "$TLS/privkey.pem.new" "$TLS/privkey.pem" && mv "$TLS/fullchain.pem.new" "$TLS/fullchain.pem"
            chmod 600 "$TLS/privkey.pem"
            return 0
        fi
    elif [ ! -s "$TLS/fullchain.pem" ]; then
        openssl req -x509 -nodes -newkey rsa:2048 -days 30 -subj "/CN=${DOMAIN:-localhost}" \
            -keyout "$TLS/privkey.pem" -out "$TLS/fullchain.pem" 2>/dev/null
        chmod 600 "$TLS/privkey.pem"
        echo "40-tls.sh: no Let's Encrypt certificate yet, temporary self-signed one in use"
    fi
    return 1
}

sync_cert || true
(
    while sleep 300; do
        if sync_cert; then
            echo "40-tls.sh: new certificate installed, reloading nginx"
            nginx -s reload || true
        fi
    done
) &

FROM node:20.18-alpine AS build
WORKDIR /app
# Exact versions from the lockfile (reproducible, tamper-evident through integrity hashes);
# install scripts disabled: no dependency code runs at install time.
COPY package.json package-lock.json ./
RUN npm ci --no-fund --ignore-scripts
COPY . .
RUN npm run build

# Unprivileged nginx (runs as uid 101, listens on 8080 for HTTP and 8443 for HTTPS).
FROM nginxinc/nginx-unprivileged:1.27-alpine
USER root
# openssl: temporary self-signed certificate until Let's Encrypt has issued one.
RUN apk add --no-cache openssl
USER 101
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --chmod=755 40-tls.sh /docker-entrypoint.d/40-tls.sh
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080 8443

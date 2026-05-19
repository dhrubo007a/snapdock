FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --frozen-lockfile
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
# Generate a self-signed certificate so HTTPS works out-of-the-box.
# Mount a volume to /certs with real cert.pem + key.pem to use a trusted cert.
RUN apk add --no-cache openssl && \
    mkdir -p /certs && \
    openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
        -keyout /certs/key.pem -out /certs/cert.pem \
        -subj "/CN=snapdock" && \
    apk del openssl
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80 443

FROM node:24-alpine AS dependencies
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:24-alpine AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=dependencies /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:24-alpine AS runtime
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
WORKDIR /app
RUN addgroup --system --gid 10002 cresta-console \
    && adduser --system --uid 10002 --ingroup cresta-console cresta-console
COPY --from=builder --chown=cresta-console:cresta-console /app/.next/standalone ./
COPY --from=builder --chown=cresta-console:cresta-console /app/.next/static ./.next/static
USER 10002:10002
EXPOSE 3000
CMD ["node", "server.js"]

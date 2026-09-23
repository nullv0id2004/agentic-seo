# seo-agents console: Next.js 15 standalone build. Read-only views plus the approvals queue.
# Runtime settings (App Service application settings): CONSOLE_DATABASE_URL (the seo_console_login
# role through the Supabase session pooler), CONSOLE_ACCESS_TOKEN, CONSOLE_APPROVER_ID, WEBSITES_PORT=8080.
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine AS run
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=8080 HOSTNAME=0.0.0.0
RUN addgroup -S app && adduser -S app -G app
COPY --from=build --chown=app:app /app/.next/standalone ./
COPY --from=build --chown=app:app /app/.next/static ./.next/static
USER app
EXPOSE 8080
CMD ["node", "server.js"]

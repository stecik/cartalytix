FROM node:24-alpine AS development
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
EXPOSE 5173
HEALTHCHECK --interval=10s --timeout=3s CMD node -e "fetch('http://127.0.0.1:5173/').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["npm", "run", "dev", "--", "--port", "5173", "--strictPort"]

FROM development AS build
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.29-alpine AS production
ENV API_UPSTREAM=api:8000 TRUSTED_PROXY=127.0.0.1
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s CMD wget -q --spider http://127.0.0.1:8080/ || exit 1

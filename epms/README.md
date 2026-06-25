# EPMS — Enterprise Procurement Management System

A web-based procure-to-pay platform for food/dairy manufacturing environments.

## Documentation

- [PRD — Product Requirements](docs/PRD.md)
- [System Design](docs/SYSTEM_DESIGN.md)

## Tech Stack

Vite · React 18 · TypeScript · Tailwind CSS v4 · Zustand · React Router v7 · React Hook Form + Zod

## Getting Started

**Local development** (requires epms-api on `:8000` — see [../epms-api/README.md](../epms-api/README.md)):

```bash
cp .env.example .env   # configure API URL (defaults work with local Docker dev setup)
npm install
npm run dev            # http://localhost:5173
```

**Production:** Built with `npm run build`, served via Nginx reverse proxy on the web server.
See [DEPLOYMENT.md](../DEPLOYMENT.md) for the distributed server layout.

## Demo Accounts

| Role | Email | Password |
|------|-------|----------|
| Requester | requester@epms.ca | password |
| Dept. Manager | manager@epms.ca | password |
| Procurement Officer | procurement@epms.ca | password |
| Warehouse Staff | warehouse@epms.ca | password |
| AP Clerk | ap@epms.ca | password |
| Finance Manager | finance@epms.ca | password |
| General Manager | gm@epms.ca | password |
| System Admin | admin@epms.ca | password |

MFA: any 6-digit code is accepted in the prototype.

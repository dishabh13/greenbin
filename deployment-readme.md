# GreenBin Deployment Guide

## 🚀 How to Run

### 1. Pull latest image

```bash
docker pull ghcr.io/dishabh13/greenbin-app:v1.2
```

### 2. Setup environment variables

Copy `.env.example` → `.env` and fill values.

### 3. Start services

```bash
docker-compose up -d
```

### 4. Access app

http://localhost:5000

---

## 🧱 Services

* greenbin → main app
* db → PostgreSQL database

---

## 🔐 Environment Variables

* SECRET_KEY → Flask secret
* GROQ_API_KEY → LLM API key
* DATABASE_URL → PostgreSQL connection string

---

## 🧪 Health Check

```bash
docker ps
docker logs greenbin
```

---

## ⚠️ Notes

* Uses PostgreSQL (not SQLite)
* Ensure port 5000 is free
* DB runs inside Docker network

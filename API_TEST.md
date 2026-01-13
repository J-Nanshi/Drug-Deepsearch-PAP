# API Testing Guide

## Quick Test in Postman

### 1. Start Research
**POST** `http://localhost:8009/api/start_research`

**Headers:**
```
Content-Type: application/json
```

**Body (JSON):**
```json
{
  "topic": "Dostarlimab"
}
```

**Expected Response:**
```json
{
  "research_id": "uuid-here",
  "status": "started",
  "message": "Research started. Use /api/progress/{research_id} to check progress."
}
```

### 2. Check Progress
**GET** `http://localhost:8009/api/progress/{research_id}`

Replace `{research_id}` with the ID from step 1.

**Expected Response:**
```json
{
  "status": "processing",
  "progress": 15,
  "stage": "Generating research plan...",
  "elapsed_time": "0m 5s"
}
```

### 3. Get Result
**GET** `http://localhost:8009/api/result/{research_id}`

### 4. Get Report
**GET** `http://localhost:8009/api/report/{research_id}`

### 5. Download Report
**GET** `http://localhost:8009/api/download/{research_id}`

## Python Test Script
Run: `python test_api.py`


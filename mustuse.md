
content = """# MUSTUSE.md — PacificaEdge
> This file tracks every single hackathon requirement.
> Every item here MUST be checked off before submitting.

---

## 1. HARD REQUIREMENT — Pacifica API (MANDATORY)

> Official rule: "Teams MUST use Pacifica API and/or Builder Code"

### Pacifica Public REST API — Used in ALL 5 Agents

| Agent | Endpoint Called | Status |
|---|---|---|
| MarketAgent | `GET https://api.pacifica.fi/api/v1/markets/summary` | ⬜ TODO |
| FundingAgent | `GET https://api.pacifica.fi/api/v1/markets/{symbol}/funding` | ⬜ TODO |
| LiquidationAgent | `GET https://api.pacifica.fi/api/v1/markets/{symbol}/trades` | ⬜ TODO |
| SignalAgent | Uses above data — no direct call | ⬜ TODO |

### How to Prove It in Demo Video
- Open browser DevTools → Network tab
- Show the actual API calls going to `api.pacifica.fi`
- Judges can SEE Pacifica endpoints being hit live ✅

---

## 2. SPONSOR TOOLS (Bonus Points — Use At Least ONE)

| Tool | What to Use It For | Required? | Status |
|---|---|---|---|
| **Elfa AI** | SentimentAgent — social sentiment score | ⭐ HIGHLY RECOMMENDED | ⬜ TODO |
| **Privy** | Wallet login button | Optional | ⬜ TODO |
| **Fuul** | Referral tracking | Optional | ⬜ TODO |
| **Rhino.fi** | Cross-chain bridge widget | Optional | ⬜ TODO |

### Elfa AI Setup (Do This First)
1. Sign up free at https://www.elfa.ai
2. Go to API Keys → Generate key
3. Add to `.env` as `ELFA_API_KEY`
4. Call `GET https://api.elfa.ai/v1/mentions/aggregations` with your key
5. Show Elfa AI logo on dashboard footer → judges see sponsor tool used

---

## 3. SUBMISSION REQUIREMENTS (ALL Mandatory)

| Requirement | Details | Status |
|---|---|---|
| ✅ Team Registration | https://forms.gle/1FP2EuvZqYiP7Tiy7 | ⬜ TODO |
| ✅ Public GitHub Repo | Must have README.md explaining project | ⬜ TODO |
| ✅ Demo Video | Max 10 minutes, uploaded to YouTube | ⬜ TODO |
| ✅ Working Project | Must work — no broken screens in demo | ⬜ TODO |
| ✅ Final Submission Form | https://forms.gle/zYm9ZBH1SoUE9t9o7 | ⬜ TODO |
| ✅ Deadline | April 16, 2026 — 9:29 PM IST | ⬜ TODO |

---

## 4. JUDGING CRITERIA — How to Score Maximum Points

| Criterion | Weight | What Judges Want to See | How We Cover It |
|---|---|---|---|
| **Innovation** | ⭐⭐⭐⭐⭐ | Never seen before on Pacifica | First multi-agent AI terminal on any perps DEX |
| **Technical Execution** | ⭐⭐⭐⭐⭐ | Code actually works, no crashes | 5 agents running live, real Pacifica API data |
| **User Experience** | ⭐⭐⭐⭐⭐ | Clean UI, easy to understand | Dark terminal dashboard, one clear BUY/SELL/HOLD |
| **Potential Impact** | ⭐⭐⭐⭐⭐ | Would real traders use this? | Every Pacifica trader needs this daily |
| **Presentation** | ⭐⭐⭐⭐⭐ | Demo video quality | 5 agents firing live = 5 wow moments |

---

## 5. TRACK — Submit Under This Track

> **Analytics & Data Track → $2,000 Prize**

Why this track:
- 5 agents = pure analytics and data intelligence
- Zero other real submissions in this track
- $2,000 is basically guaranteed ✅

---

## 6. WHAT MUST BE VISIBLE IN DEMO VIDEO

These must appear ON SCREEN during your demo video:

- [ ] Pacifica API being called (show network tab OR show live data updating)
- [ ] At least ONE Pacifica market (BTC-USDC, ETH-USDC, or SOL-USDC)
- [ ] All 5 agent outputs visible on dashboard
- [ ] Final BUY / SELL / HOLD signal displayed
- [ ] Elfa AI sentiment score shown (proves sponsor tool usage)
- [ ] Your name / project name "PacificaEdge" visible

---

## 7. README.md MUST INCLUDE (For GitHub)

```markdown
# PacificaEdge

## What it does
[1 paragraph explanation]

## Pacifica API Usage
- Agent 1 uses: GET /markets/summary
- Agent 2 uses: GET /markets/{symbol}/funding
- Agent 3 uses: GET /markets/{symbol}/trades

## Sponsor Tools Used
- Elfa AI: Social sentiment scoring

## How to run
pip install -r requirements.txt
uvicorn main:app --reload

## Demo Video
[YouTube link]

## Track
Analytics & Data
```

---

## 8. THINGS THAT WILL GET YOU DISQUALIFIED

- ❌ Not using Pacifica API at all
- ❌ Submitting after April 16, 9:29 PM IST
- ❌ No demo video
- ❌ Private GitHub repo (must be public)
- ❌ Project built before hackathon start date (March 16, 2026)

---

## 9. FINAL SUBMISSION CHECKLIST

Complete these IN ORDER on April 16:

- [ ] 1. Project runs without errors locally
- [ ] 2. All 5 agents return real Pacifica data
- [ ] 3. GitHub repo is PUBLIC with README
- [ ] 4. Record demo video (5-10 min) → upload to YouTube
- [ ] 5. Fill submission form: https://forms.gle/zYm9ZBH1SoUE9t9o7
- [ ] 6. Submit BEFORE 9:29 PM IST ⏰

---

*PacificaEdge — Hackathon Requirements Tracker*
"""


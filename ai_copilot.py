import os, json
from pathlib import Path

import joblib
import requests
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

MODEL_PATH = Path(__file__).resolve().parent / "models_dynamic" / "copilot_intent_model.pkl"

TRAINING_DATA = {
    "WHY_RISK": [
        "why is this student at risk",
        "why was this student flagged",
        "explain the risk",
        "what caused this prediction",
        "why this prediction",
        "show risk evidence",
        "why is the student critical",
    ],
    "SUPPORT": [
        "what support should we give",
        "recommend intervention",
        "what should the adviser do",
        "what academic support is needed",
        "what should we prioritise",
        "recommend help for this student",
    ],
    "TRAJECTORY": [
        "explain academic history",
        "show academic performance",
        "show semester result",
        "show semester by semester result",
        "show performance from 100 level",
        "compare earlier levels",
        "is the student improving",
        "is performance declining",
        "explain academic trajectory",
        "acdemi performance",
        "acdemic history",
    ],
    "PREDICTION": [
        "what is the predicted next gpa",
        "what is the predicted final cgpa",
        "predict future performance",
        "what will the student likely score next",
        "show forecast",
    ],
    "FOLLOWUP": [
        "has the intervention worked",
        "show follow up progress",
        "is the student improving after support",
        "what is the latest follow up",
        "compare progress after intervention",
    ],
    "SUMMARY": [
        "summarise this student",
        "give me an academic brief",
        "explain this student",
        "student overview",
        "academic summary",
        "tell me about this student",
        "acdemi",
        "academic",
    ],
}

def _train_intent_model():
    texts, labels = [], []
    for label, samples in TRAINING_DATA.items():
        texts.extend(samples)
        labels.extend([label] * len(samples))

    # Character n-grams tolerate common spelling mistakes.
    model = Pipeline([
        ("tfidf", TfidfVectorizer(analyzer="char_wb", ngram_range=(3,5), lowercase=True)),
        ("classifier", LogisticRegression(max_iter=1500, random_state=42)),
    ])
    model.fit(texts, labels)

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        joblib.dump(model, MODEL_PATH)
    except Exception:
        pass
    return model

# Re-train this small project-specific intent model when the app starts.
INTENT_MODEL = _train_intent_model()

def _cfg():
    return {
        "base_url": os.getenv("EDUPULSE_AI_BASE_URL","").rstrip("/"),
        "api_key": os.getenv("EDUPULSE_AI_API_KEY",""),
        "model": os.getenv("EDUPULSE_AI_MODEL",""),
    }

def external_available():
    c=_cfg()
    return bool(c["base_url"] and c["api_key"] and c["model"])

def _safe_num(v):
    try:
        return float(v)
    except Exception:
        return None

def build_context(student, records, prediction, interventions, followups, school_scale):
    clean=[]
    for r in records:
        clean.append({
            "session":r.get("academic_session"),
            "level":r.get("level"),
            "semester":r.get("semester"),
            "gpa":_safe_num(r.get("gpa")),
            "failed_courses":r.get("failed_courses"),
            "carryovers":r.get("carryovers"),
            "attendance_rate":_safe_num(r.get("attendance_rate")),
        })

    return {
        "student":{
            "course":student.get("programme"),
            "level":student.get("level"),
            "programme_duration":student.get("programme_duration"),
            "gpa_scale":float(school_scale),
        },
        "records":clean,
        "prediction":prediction,
        "interventions":interventions[:8],
        "followups":followups[:8],
    }

def baseline_brief(c):
    s=c["student"]
    return (
        f"This student is currently in {s.get('level')} Level studying {s.get('course') or 'the registered course'}. "
        "No official semester result is available yet, so EduPulse has not generated a predictive risk."
    )

def _trajectory(c):
    records=c.get("records") or []
    scale=float(c["student"].get("gpa_scale") or 5.0)
    vals=[r["gpa"] for r in records if r.get("gpa") is not None]

    if not vals:
        return baseline_brief(c)

    if len(vals)==1:
        return f"Only one semester result is available: {vals[0]:.2f}/{scale:.1f}."

    change=vals[-1]-vals[0]
    if change > scale*0.05:
        direction="improving"
    elif change < -scale*0.05:
        direction="declining"
    else:
        direction="fairly stable"

    return (
        f"The student's performance is {direction}, moving from "
        f"{vals[0]:.2f} to {vals[-1]:.2f} across the recorded semesters."
    )

def local_brief(c):
    p=c.get("prediction")
    if not p or not p.get("available",True):
        return baseline_brief(c)

    scale=float(c["student"].get("gpa_scale") or 5.0)
    text=(
        f"{_trajectory(c)} "
        f"EduPulse currently places the student in the {p.get('risk_level','Unknown').title()} risk category "
        f"with an estimated risk of {p.get('risk_probability',0):.0%}. "
        f"The predicted final CGPA is {p.get('predicted_final_cgpa',0):.2f}/{scale:.1f}."
    )

    if p.get("predicted_next_gpa") is not None:
        text += f" The predicted next GPA is {p['predicted_next_gpa']:.2f}/{scale:.1f}."

    return text

def _intent(question):
    q=(question or "").strip().lower()

    if not q:
        return "SUMMARY"

    # Domain shortcuts first. "acdemi" and similar spelling still resolve correctly.
    if any(k in q for k in ["support","intervention","help","priorit"]):
        return "SUPPORT"
    if any(k in q for k in ["next gpa","final cgpa","forecast","predict"]):
        return "PREDICTION"
    if any(k in q for k in ["follow","after support","intervention work"]):
        return "FOLLOWUP"
    if any(k in q for k in ["history","semester","100 level","200 level","300 level","400 level","500 level",
                             "trajectory","improv","declin","academic","acdem"]):
        return "TRAJECTORY"
    if any(k in q for k in ["why","risk","flag","reason","evidence","critical"]):
        return "WHY_RISK"

    try:
        probs=INTENT_MODEL.predict_proba([q])[0]
        classes=INTENT_MODEL.classes_
        idx=int(probs.argmax())
        # Unknown questions should return a student summary, not a random risk explanation.
        return str(classes[idx]) if float(probs[idx]) >= 0.34 else "SUMMARY"
    except Exception:
        return "SUMMARY"

def local_answer(question,c):
    p=c.get("prediction")
    if not p or not p.get("available",True):
        return baseline_brief(c)

    intent=_intent(question)
    scale=float(c["student"].get("gpa_scale") or 5.0)

    if intent=="WHY_RISK":
        factors=[x for x in (p.get("factors") or []) if isinstance(x,str) and x.strip()]
        generic="No single issue dominates the prediction"
        factors=[x for x in factors if generic.lower() not in x.lower()]
        if factors:
            return "\n".join(f"• {x}" for x in factors)

        records=c.get("records") or []
        if records:
            latest=records[-1]
            parts=[_trajectory(c)]
            if int(latest.get("failed_courses") or 0)>0:
                parts.append(f"The latest result has {int(latest.get('failed_courses') or 0)} failed course(s).")
            if int(latest.get("carryovers") or 0)>0:
                parts.append(f"The latest result has {int(latest.get('carryovers') or 0)} carryover(s).")
            if latest.get("attendance_rate") is not None and float(latest["attendance_rate"])<70:
                parts.append(f"Latest attendance is {float(latest['attendance_rate']):.0f}%.")
            return " ".join(parts)

        return local_brief(c)

    if intent=="SUPPORT":
        recs=[x for x in (p.get("recommendations") or []) if isinstance(x,str) and x.strip()]
        return "\n".join(f"• {x}" for x in recs) if recs else "Continue academic monitoring."

    if intent=="TRAJECTORY":
        records=c.get("records") or []
        if not records:
            return baseline_brief(c)

        lines=[_trajectory(c), ""]
        for r in records:
            if r.get("gpa") is not None:
                lines.append(
                    f"{r.get('level')}L {r.get('semester')} Semester: "
                    f"GPA {float(r['gpa']):.2f}/{scale:.1f}"
                )
        return "\n".join(lines)

    if intent=="PREDICTION":
        text=f"Predicted final CGPA: {p.get('predicted_final_cgpa',0):.2f}/{scale:.1f}."
        if p.get("predicted_next_gpa") is not None:
            text+=f" Predicted next GPA: {p['predicted_next_gpa']:.2f}/{scale:.1f}."
        return text

    if intent=="FOLLOWUP":
        f=c.get("followups") or []
        if not f:
            return "No intervention follow-up has been recorded yet."
        latest=f[0]
        return f"Latest follow-up: {latest.get('progress_status','Not classified')}. {latest.get('observation_note') or ''}".strip()

    return local_brief(c)

def _external(question,c):
    cfg=_cfg()
    system=(
        "You are EduPulse Academic Copilot. Answer only from the supplied student's academic evidence. "
        "Do not invent facts. Respect the institution GPA scale. Keep answers brief and school-friendly. "
        "Handle spelling mistakes naturally."
    )
    payload={
        "model":cfg["model"],
        "messages":[
            {"role":"system","content":system},
            {"role":"user","content":"Evidence:\n"+json.dumps(c,default=str)+"\n\nQuestion:\n"+question}
        ],
        "temperature":0.15,
    }
    r=requests.post(
        cfg["base_url"]+"/chat/completions",
        headers={"Authorization":f"Bearer {cfg['api_key']}","Content-Type":"application/json"},
        json=payload,timeout=45
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

def answer(question,c):
    if external_available():
        try:
            return _external(question,c)
        except Exception:
            pass
    return local_answer(question,c)

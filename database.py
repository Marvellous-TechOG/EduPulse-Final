from __future__ import annotations
import re

import os
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("EDUPULSE_DB_PATH", str(BASE_DIR / "data" / "edupulse.db")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db():
    schema = """
    CREATE TABLE IF NOT EXISTS schools (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        code TEXT NOT NULL UNIQUE,
        gpa_scale REAL NOT NULL DEFAULT 5.0,
        default_programme_duration INTEGER NOT NULL DEFAULT 4,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        school_id INTEGER,
        full_name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        created_by INTEGER,
        created_at TEXT NOT NULL,
        last_login TEXT,
        FOREIGN KEY (school_id) REFERENCES schools(id)
    );

    CREATE TABLE IF NOT EXISTS invite_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        school_id INTEGER NOT NULL,
        code_hash TEXT NOT NULL UNIQUE,
        role TEXT NOT NULL,
        expires_at TEXT,
        used INTEGER NOT NULL DEFAULT 0,
        used_by INTEGER,
        created_by INTEGER,
        created_at TEXT NOT NULL,
        FOREIGN KEY (school_id) REFERENCES schools(id)
    );

    CREATE TABLE IF NOT EXISTS role_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        school_id INTEGER NOT NULL,
        role TEXT NOT NULL,
        code_hash TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(school_id, role),
        FOREIGN KEY (school_id) REFERENCES schools(id)
    );

    CREATE TABLE IF NOT EXISTS students (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        school_id INTEGER NOT NULL,
        student_id TEXT,
        matric_no TEXT NOT NULL,
        full_name TEXT NOT NULL,
        gender TEXT,
        programme TEXT NOT NULL,
        department TEXT,
        faculty TEXT,
        level INTEGER NOT NULL DEFAULT 100,
        programme_duration INTEGER NOT NULL DEFAULT 4,
        academic_session TEXT,
        entry_year INTEGER,
        study_mode TEXT,
        student_email TEXT,
        parent_name TEXT,
        parent_email TEXT,
        guardian_phone TEXT,
        admission_score REAL,
        created_by INTEGER NOT NULL,
        assigned_staff_id INTEGER,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(school_id, matric_no),
        FOREIGN KEY (school_id) REFERENCES schools(id),
        FOREIGN KEY (created_by) REFERENCES users(id),
        FOREIGN KEY (assigned_staff_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS semester_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER NOT NULL,
        academic_session TEXT NOT NULL,
        level INTEGER NOT NULL,
        semester TEXT NOT NULL,
        gpa REAL NOT NULL,
        credits_registered REAL,
        credits_earned REAL,
        failed_courses INTEGER DEFAULT 0,
        carryovers INTEGER DEFAULT 0,
        attendance_rate REAL,
        ca_average REAL,
        exam_average REAL,
        recorded_by INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(student_id, academic_session, level, semester),
        FOREIGN KEY (student_id) REFERENCES students(id),
        FOREIGN KEY (recorded_by) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS course_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        semester_record_id INTEGER NOT NULL,
        course_code TEXT,
        course_title TEXT,
        credit_unit REAL,
        ca_score REAL,
        exam_score REAL,
        total_score REAL,
        recorded_by INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (semester_record_id) REFERENCES semester_records(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER NOT NULL,
        semester_record_id INTEGER,
        model_version TEXT,
        predicted_next_gpa REAL,
        predicted_final_cgpa REAL,
        risk_probability REAL NOT NULL,
        risk_level TEXT NOT NULL,
        factors_json TEXT,
        created_by INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (student_id) REFERENCES students(id),
        FOREIGN KEY (semester_record_id) REFERENCES semester_records(id),
        FOREIGN KEY (created_by) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS watchlist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        school_id INTEGER NOT NULL,
        student_id INTEGER NOT NULL,
        prediction_id INTEGER NOT NULL,
        risk_level TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'OPEN',
        assigned_to INTEGER,
        created_by INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        resolved_at TEXT,
        UNIQUE(school_id, student_id),
        FOREIGN KEY (school_id) REFERENCES schools(id),
        FOREIGN KEY (student_id) REFERENCES students(id),
        FOREIGN KEY (prediction_id) REFERENCES predictions(id)
    );

    CREATE TABLE IF NOT EXISTS interventions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        school_id INTEGER NOT NULL,
        student_id INTEGER NOT NULL,
        watchlist_id INTEGER,
        intervention_type TEXT NOT NULL,
        description TEXT,
        owner_user_id INTEGER,
        status TEXT NOT NULL DEFAULT 'PLANNED',
        start_date TEXT,
        review_date TEXT,
        adviser_note TEXT,
        parent_notification INTEGER NOT NULL DEFAULT 0,
        created_by INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (school_id) REFERENCES schools(id),
        FOREIGN KEY (student_id) REFERENCES students(id),
        FOREIGN KEY (watchlist_id) REFERENCES watchlist(id)
    );

    CREATE TABLE IF NOT EXISTS followups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        school_id INTEGER NOT NULL,
        student_id INTEGER NOT NULL,
        intervention_id INTEGER,
        observation_date TEXT NOT NULL,
        observed_gpa REAL,
        observed_cgpa REAL,
        attendance_rate REAL,
        progress_status TEXT,
        observation_note TEXT,
        recorded_by INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (school_id) REFERENCES schools(id),
        FOREIGN KEY (student_id) REFERENCES students(id),
        FOREIGN KEY (intervention_id) REFERENCES interventions(id)
    );

    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        school_id INTEGER NOT NULL,
        student_id INTEGER,
        recipient_type TEXT,
        recipient_email TEXT,
        subject TEXT NOT NULL,
        body TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'QUEUED',
        created_by INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        sent_at TEXT,
        FOREIGN KEY (school_id) REFERENCES schools(id),
        FOREIGN KEY (student_id) REFERENCES students(id)
    );

    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        school_id INTEGER,
        user_id INTEGER,
        action TEXT NOT NULL,
        entity_type TEXT,
        entity_id TEXT,
        details TEXT,
        created_at TEXT NOT NULL
    );
    """
    with connect() as conn:
        conn.executescript(schema)
    cols=[r["name"] for r in conn.execute("PRAGMA table_info(schools)").fetchall()]
    if "current_academic_session" not in cols:
        conn.execute("ALTER TABLE schools ADD COLUMN current_academic_session TEXT DEFAULT '2025/2026'")
    conn.commit()


def execute(sql, params=()):
    with connect() as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


def query(sql, params=()):
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def query_one(sql, params=()):
    with connect() as conn:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None


def log_action(user, action, entity_type=None, entity_id=None, details=None):
    execute(
        """INSERT INTO audit_log
           (school_id,user_id,action,entity_type,entity_id,details,created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (
            user.get("school_id") if user else None,
            user.get("id") if user else None,
            action,
            entity_type,
            str(entity_id) if entity_id is not None else None,
            details,
            now_iso(),
        ),
    )


def create_school(name, code, gpa_scale, default_duration):
    return execute(
        """INSERT INTO schools(name,code,gpa_scale,default_programme_duration,created_at)
           VALUES (?,?,?,?,?)""",
        (name.strip(), code.strip().upper(), float(gpa_scale), int(default_duration), now_iso()),
    )


def get_school(school_id):
    return query_one("SELECT * FROM schools WHERE id=?", (school_id,))


def get_school_by_code(code):
    return query_one("SELECT * FROM schools WHERE code=?", (code.strip().upper(),))


def update_school(school_id, name, gpa_scale, default_duration):
    execute(
        """UPDATE schools SET name=?, gpa_scale=?, default_programme_duration=? WHERE id=?""",
        (name.strip(), float(gpa_scale), int(default_duration), school_id),
    )


def create_user(school_id, full_name, email, password_hash, role, created_by=None):
    return execute(
        """INSERT INTO users(school_id,full_name,email,password_hash,role,created_by,created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (
            school_id,
            full_name.strip(),
            email.strip().lower(),
            password_hash,
            role,
            created_by,
            now_iso(),
        ),
    )


def get_user_by_email(email):
    return query_one("SELECT * FROM users WHERE lower(email)=lower(?)", (email.strip(),))


def get_user(user_id):
    return query_one("SELECT * FROM users WHERE id=?", (user_id,))


def list_users(school_id=None):
    if school_id is None:
        return query(
            """SELECT u.*, s.name AS school_name FROM users u
               LEFT JOIN schools s ON s.id=u.school_id
               ORDER BY u.created_at DESC"""
        )
    return query(
        """SELECT u.*, s.name AS school_name FROM users u
           LEFT JOIN schools s ON s.id=u.school_id
           WHERE u.school_id=? ORDER BY u.full_name""",
        (school_id,),
    )


def set_user_active(user_id, active):
    execute("UPDATE users SET active=? WHERE id=?", (1 if active else 0, user_id))


def update_last_login(user_id):
    execute("UPDATE users SET last_login=? WHERE id=?", (now_iso(), user_id))


def create_invite(school_id, code_hash, role, created_by, expires_at=None):
    return execute(
        """INSERT INTO invite_codes
           (school_id,code_hash,role,expires_at,created_by,created_at)
           VALUES (?,?,?,?,?,?)""",
        (school_id, code_hash, role, expires_at, created_by, now_iso()),
    )


def get_invite(code_hash):
    return query_one("SELECT * FROM invite_codes WHERE code_hash=?", (code_hash,))


def consume_invite(invite_id, user_id):
    execute(
        "UPDATE invite_codes SET used=1, used_by=? WHERE id=?",
        (user_id, invite_id),
    )


def upsert_student(data, user_id, school_id):
    existing = query_one(
        "SELECT * FROM students WHERE school_id=? AND matric_no=?",
        (school_id, data["matric_no"].strip()),
    )
    values = (
        data.get("student_id"),
        data["matric_no"].strip(),
        data["full_name"].strip(),
        data.get("gender"),
        data["programme"].strip(),
        data.get("department"),
        data.get("faculty"),
        int(data.get("level", 100)),
        int(data.get("programme_duration", 4)),
        data.get("academic_session"),
        data.get("entry_year"),
        data.get("study_mode"),
        data.get("student_email"),
        data.get("parent_name"),
        data.get("parent_email"),
        data.get("guardian_phone"),
        data.get("admission_score"),
        data.get("assigned_staff_id"),
        now_iso(),
    )
    if existing:
        execute(
            """UPDATE students SET
               student_id=?, matric_no=?, full_name=?, gender=?, programme=?,
               department=?, faculty=?, level=?, programme_duration=?,
               academic_session=?, entry_year=?, study_mode=?, student_email=?,
               parent_name=?, parent_email=?, guardian_phone=?, admission_score=?,
               assigned_staff_id=?, updated_at=?
               WHERE id=?""",
            values + (existing["id"],),
        )
        return existing["id"]

    return execute(
        """INSERT INTO students(
           school_id,student_id,matric_no,full_name,gender,programme,department,
           faculty,level,programme_duration,academic_session,entry_year,study_mode,
           student_email,parent_name,parent_email,guardian_phone,admission_score,
           created_by,assigned_staff_id,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            school_id,
            data.get("student_id"),
            data["matric_no"].strip(),
            data["full_name"].strip(),
            data.get("gender"),
            data["programme"].strip(),
            data.get("department"),
            data.get("faculty"),
            int(data.get("level", 100)),
            int(data.get("programme_duration", 4)),
            data.get("academic_session"),
            data.get("entry_year"),
            data.get("study_mode"),
            data.get("student_email"),
            data.get("parent_name"),
            data.get("parent_email"),
            data.get("guardian_phone"),
            data.get("admission_score"),
            user_id,
            data.get("assigned_staff_id"),
            now_iso(),
            now_iso(),
        ),
    )


def get_student(student_id):
    return query_one("SELECT * FROM students WHERE id=?", (student_id,))


def list_students_for_user(user, view_all=False):
    if user["role"] == "SUPERADMIN" and user.get("school_id") is None:
        return query("SELECT * FROM students WHERE active=1 ORDER BY full_name")
    if view_all:
        return query(
            "SELECT * FROM students WHERE school_id=? AND active=1 ORDER BY full_name",
            (user["school_id"],),
        )
    return query(
        """SELECT * FROM students
           WHERE school_id=? AND active=1
             AND (created_by=? OR assigned_staff_id=?)
           ORDER BY full_name""",
        (user["school_id"], user["id"], user["id"]),
    )


def add_semester_record(student_id, data, user_id):
    existing = query_one(
        """SELECT id FROM semester_records
           WHERE student_id=? AND academic_session=? AND level=? AND semester=?""",
        (student_id, data["academic_session"], int(data["level"]), data["semester"]),
    )
    params = (
        float(data["gpa"]),
        data.get("credits_registered"),
        data.get("credits_earned"),
        int(data.get("failed_courses", 0) or 0),
        int(data.get("carryovers", 0) or 0),
        data.get("attendance_rate"),
        data.get("ca_average"),
        data.get("exam_average"),
        user_id,
        now_iso(),
    )
    if existing:
        execute(
            """UPDATE semester_records SET gpa=?,credits_registered=?,credits_earned=?,
               failed_courses=?,carryovers=?,attendance_rate=?,ca_average=?,exam_average=?,
               recorded_by=?,created_at=? WHERE id=?""",
            params + (existing["id"],),
        )
        return existing["id"]

    return execute(
        """INSERT INTO semester_records(
           student_id,academic_session,level,semester,gpa,credits_registered,
           credits_earned,failed_courses,carryovers,attendance_rate,ca_average,
           exam_average,recorded_by,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            student_id,
            data["academic_session"],
            int(data["level"]),
            data["semester"],
        ) + params,
    )


def clear_course_scores(semester_record_id):
    execute("DELETE FROM course_scores WHERE semester_record_id=?", (semester_record_id,))


def add_course_score(semester_record_id, row, user_id):
    total = None
    if row.get("ca_score") is not None or row.get("exam_score") is not None:
        total = float(row.get("ca_score") or 0) + float(row.get("exam_score") or 0)
    execute(
        """INSERT INTO course_scores(
           semester_record_id,course_code,course_title,credit_unit,ca_score,
           exam_score,total_score,recorded_by,created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            semester_record_id,
            row.get("course_code"),
            row.get("course_title"),
            row.get("credit_unit"),
            row.get("ca_score"),
            row.get("exam_score"),
            total,
            user_id,
            now_iso(),
        ),
    )


def list_semester_records(student_id):
    return query(
        """SELECT * FROM semester_records WHERE student_id=?
           ORDER BY academic_session, level,
           CASE semester WHEN 'First' THEN 1 ELSE 2 END, created_at""",
        (student_id,),
    )


def latest_semester_record(student_id):
    return query_one(
        """SELECT * FROM semester_records WHERE student_id=?
           ORDER BY created_at DESC LIMIT 1""",
        (student_id,),
    )


def save_prediction(student_id, record_id, result, user_id):
    import json
    return execute(
        """INSERT INTO predictions(
           student_id,semester_record_id,model_version,predicted_next_gpa,
           predicted_final_cgpa,risk_probability,risk_level,factors_json,
           created_by,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            student_id,
            record_id,
            result.get("model_version"),
            result.get("predicted_next_gpa"),
            result.get("predicted_final_cgpa"),
            float(result["risk_probability"]),
            result["risk_level"],
            json.dumps(result.get("factors", [])),
            user_id,
            now_iso(),
        ),
    )


def latest_prediction(student_id):
    return query_one(
        """SELECT * FROM predictions WHERE student_id=?
           ORDER BY created_at DESC LIMIT 1""",
        (student_id,),
    )


def upsert_watchlist(school_id, student_id, prediction_id, risk_level, user_id):
    existing = query_one(
        "SELECT * FROM watchlist WHERE school_id=? AND student_id=?",
        (school_id, student_id),
    )
    if existing:
        execute(
            """UPDATE watchlist SET prediction_id=?,risk_level=?,status='OPEN',
               updated_at=?,resolved_at=NULL WHERE id=?""",
            (prediction_id, risk_level, now_iso(), existing["id"]),
        )
        return existing["id"]
    return execute(
        """INSERT INTO watchlist(
           school_id,student_id,prediction_id,risk_level,status,created_by,
           created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            school_id, student_id, prediction_id, risk_level, "OPEN",
            user_id, now_iso(), now_iso()
        ),
    )


def resolve_watchlist(school_id, student_id):
    execute(
        """UPDATE watchlist SET status='RESOLVED',resolved_at=?,updated_at=?
           WHERE school_id=? AND student_id=?""",
        (now_iso(), now_iso(), school_id, student_id),
    )


def list_watchlist(school_id, include_resolved=False):
    sql = """
    SELECT w.*, s.matric_no, s.full_name, s.programme, s.level,
           p.risk_probability, p.predicted_next_gpa, p.predicted_final_cgpa,
           p.factors_json
    FROM watchlist w
    JOIN students s ON s.id=w.student_id
    JOIN predictions p ON p.id=w.prediction_id
    WHERE w.school_id=?
    """
    if not include_resolved:
        sql += " AND w.status='OPEN'"
    sql += " ORDER BY p.risk_probability DESC, w.updated_at DESC"
    return query(sql, (school_id,))


def add_intervention(data):
    return execute(
        """INSERT INTO interventions(
           school_id,student_id,watchlist_id,intervention_type,description,
           owner_user_id,status,start_date,review_date,adviser_note,
           parent_notification,created_by,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            data["school_id"], data["student_id"], data.get("watchlist_id"),
            data["intervention_type"], data.get("description"),
            data.get("owner_user_id"), data.get("status", "PLANNED"),
            data.get("start_date"), data.get("review_date"),
            data.get("adviser_note"), 1 if data.get("parent_notification") else 0,
            data["created_by"], now_iso(), now_iso()
        ),
    )


def list_interventions(school_id, student_id=None):
    sql = """
    SELECT i.*, s.matric_no, s.full_name, s.programme,
           u.full_name AS owner_name
    FROM interventions i
    JOIN students s ON s.id=i.student_id
    LEFT JOIN users u ON u.id=i.owner_user_id
    WHERE i.school_id=?
    """
    params = [school_id]
    if student_id is not None:
        sql += " AND i.student_id=?"
        params.append(student_id)
    sql += " ORDER BY i.created_at DESC"
    return query(sql, tuple(params))


def update_intervention_status(intervention_id, status):
    execute(
        "UPDATE interventions SET status=?,updated_at=? WHERE id=?",
        (status, now_iso(), intervention_id),
    )


def add_followup(data):
    return execute(
        """INSERT INTO followups(
           school_id,student_id,intervention_id,observation_date,observed_gpa,
           observed_cgpa,attendance_rate,progress_status,observation_note,
           recorded_by,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            data["school_id"], data["student_id"], data.get("intervention_id"),
            data["observation_date"], data.get("observed_gpa"),
            data.get("observed_cgpa"), data.get("attendance_rate"),
            data.get("progress_status"), data.get("observation_note"),
            data["recorded_by"], now_iso()
        ),
    )


def list_followups(school_id, student_id=None):
    sql = """
    SELECT f.*, s.matric_no, s.full_name, s.programme,
           i.intervention_type
    FROM followups f
    JOIN students s ON s.id=f.student_id
    LEFT JOIN interventions i ON i.id=f.intervention_id
    WHERE f.school_id=?
    """
    params = [school_id]
    if student_id is not None:
        sql += " AND f.student_id=?"
        params.append(student_id)
    sql += " ORDER BY f.observation_date DESC, f.created_at DESC"
    return query(sql, tuple(params))


def add_notification(data):
    return execute(
        """INSERT INTO notifications(
           school_id,student_id,recipient_type,recipient_email,subject,body,
           status,created_by,created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            data["school_id"], data.get("student_id"), data.get("recipient_type"),
            data.get("recipient_email"), data["subject"], data["body"],
            data.get("status", "QUEUED"), data["created_by"], now_iso()
        ),
    )


def list_notifications(school_id):
    return query(
        "SELECT * FROM notifications WHERE school_id=? ORDER BY created_at DESC",
        (school_id,),
    )


def school_counts(school_id):
    return {
        "students": query_one("SELECT COUNT(*) AS n FROM students WHERE school_id=? AND active=1", (school_id,))["n"],
        "watchlist": query_one("SELECT COUNT(*) AS n FROM watchlist WHERE school_id=? AND status='OPEN'", (school_id,))["n"],
        "interventions": query_one("SELECT COUNT(*) AS n FROM interventions WHERE school_id=?", (school_id,))["n"],
        "followups": query_one("SELECT COUNT(*) AS n FROM followups WHERE school_id=?", (school_id,))["n"],
    }


def set_role_code(school_id, role, code_hash):
    existing = query_one("SELECT id FROM role_codes WHERE school_id=? AND role=?", (school_id, role))
    if existing:
        execute("UPDATE role_codes SET code_hash=?, updated_at=? WHERE id=?", (code_hash, now_iso(), existing["id"]))
    else:
        execute("INSERT INTO role_codes(school_id,role,code_hash,updated_at) VALUES (?,?,?,?)", (school_id, role, code_hash, now_iso()))

def get_role_code(school_id, role):
    return query_one("SELECT * FROM role_codes WHERE school_id=? AND role=?", (school_id, role))

def update_user_password(user_id, password_hash):
    execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id))

def update_user_role(user_id, role):
    """Update an existing user's institutional role."""
    execute("UPDATE users SET role=? WHERE id=?", (role, user_id))

def recent_audit(school_id, limit=12):
    return query("""SELECT a.*, u.full_name FROM audit_log a LEFT JOIN users u ON u.id=a.user_id WHERE a.school_id=? ORDER BY a.created_at DESC LIMIT ?""", (school_id, int(limit)))


def rescale_school_academics(school_id, old_scale, new_scale):
    """Convert stored GPA-like values proportionally when the institution changes scale."""
    old_scale=float(old_scale)
    new_scale=float(new_scale)
    if old_scale <= 0 or new_scale <= 0 or abs(old_scale-new_scale) < 1e-9:
        return
    ratio=new_scale/old_scale
    with connect() as conn:
        conn.execute(
            """UPDATE semester_records SET gpa = ROUND(gpa * ?, 4)
               WHERE student_id IN (SELECT id FROM students WHERE school_id=?)""",
            (ratio, school_id),
        )
        conn.execute(
            """UPDATE followups SET
               observed_gpa = CASE WHEN observed_gpa IS NULL THEN NULL ELSE ROUND(observed_gpa * ?,4) END,
               observed_cgpa = CASE WHEN observed_cgpa IS NULL THEN NULL ELSE ROUND(observed_cgpa * ?,4) END
               WHERE school_id=?""",
            (ratio, ratio, school_id),
        )
        conn.execute(
            """UPDATE predictions SET
               predicted_next_gpa = CASE WHEN predicted_next_gpa IS NULL THEN NULL ELSE ROUND(predicted_next_gpa * ?,4) END,
               predicted_final_cgpa = CASE WHEN predicted_final_cgpa IS NULL THEN NULL ELSE ROUND(predicted_final_cgpa * ?,4) END
               WHERE student_id IN (SELECT id FROM students WHERE school_id=?)""",
            (ratio, ratio, school_id),
        )
        conn.commit()


def update_school_settings(school_id, name, code, gpa_scale, default_duration, current_academic_session):
    """Director/Rector reconfiguration of institution settings."""
    current=get_school(school_id)
    if not current:
        raise ValueError("Institution was not found.")

    name=(name or "").strip()
    code=(code or "").strip().upper()
    session=(current_academic_session or "").strip()
    new_scale=float(gpa_scale)
    duration=int(default_duration)

    if not name:
        raise ValueError("Institution name is required.")
    if not code:
        raise ValueError("Institution code is required.")
    if new_scale not in (4.0,5.0):
        raise ValueError("GPA scale must be 4.0 or 5.0.")
    if duration not in (4,5):
        raise ValueError("Course duration must be 4 or 5 years.")
    if not re.match(r"^\d{4}/\d{4}$", session):
        raise ValueError("Academic session should look like 2025/2026.")

    duplicate=query_one("SELECT id FROM schools WHERE code=? AND id<>?",(code,school_id))
    if duplicate:
        raise ValueError("That institution code is already in use.")

    old_scale=float(current["gpa_scale"])
    if abs(old_scale-new_scale)>1e-9:
        rescale_school_academics(school_id,old_scale,new_scale)

    execute(
        """UPDATE schools
           SET name=?, code=?, gpa_scale=?, default_programme_duration=?, current_academic_session=?
           WHERE id=?""",
        (name,code,new_scale,duration,session,school_id)
    )

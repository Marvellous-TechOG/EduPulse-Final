import hmac, hashlib, secrets
import database as db

ROLES=["PRINCIPAL","VICE_PRINCIPAL","INTERVENTION_COMMITTEE","ACADEMIC_ADVISER","LECTURER"]
LABELS={"PRINCIPAL":"Director","VICE_PRINCIPAL":"Rector","INTERVENTION_COMMITTEE":"Intervention Committee","ACADEMIC_ADVISER":"Academic Adviser","LECTURER":"Lecturer","SUPERADMIN":"System Administrator"}
PERMISSIONS={
"SUPERADMIN":{"view_all_students","register_students","record_results","assess_students","bulk_screen","view_watchlist","manage_interventions","followup","reports","ai_copilot","manage_users","manage_school","view_school_settings"},
"PRINCIPAL":{"view_all_students","register_students","record_results","assess_students","bulk_screen","view_watchlist","manage_interventions","followup","reports","ai_copilot","manage_users","manage_school","view_school_settings"},
"VICE_PRINCIPAL":{"view_all_students","register_students","record_results","assess_students","bulk_screen","view_watchlist","manage_interventions","followup","reports","ai_copilot","manage_users","manage_school","view_school_settings"},
"INTERVENTION_COMMITTEE":{"view_all_students","assess_students","view_watchlist","manage_interventions","followup","reports","ai_copilot","view_school_settings"},
"ACADEMIC_ADVISER":{"view_all_students","register_students","record_results","assess_students","bulk_screen","view_watchlist","manage_interventions","followup","reports","ai_copilot","view_school_settings"},
"LECTURER":{"register_students","record_results","assess_students","bulk_screen","ai_copilot"}}

def role_label(role): return LABELS.get(role, role.replace('_',' ').title())
def can(user,permission): return bool(user) and permission in PERMISSIONS.get(user.get('role'),set())
def hash_password(password):
    salt=secrets.token_bytes(16); digest=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,210000); return f"{salt.hex()}${digest.hex()}"
def verify_password(password,stored):
    try:
        sh,dh=stored.split('$',1); actual=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(sh),210000); return hmac.compare_digest(actual,bytes.fromhex(dh))
    except Exception: return False
def code_hash(code): return hashlib.sha256(str(code or '').strip().encode()).hexdigest()

def login(email,password):
    u=db.get_user_by_email(email)
    if not u or not u.get('active') or not verify_password(password,u['password_hash']): return None
    db.update_last_login(u['id']); return db.get_user(u['id'])

def setup_institution(name,school_code,gpa_scale,duration,owner_name,owner_email,owner_password,role_codes):
    sid=db.create_school(name,school_code,gpa_scale,duration)
    uid=db.create_user(sid,owner_name,owner_email,hash_password(owner_password),'SUPERADMIN')
    for role,raw in role_codes.items(): db.set_role_code(sid,role,code_hash(raw))
    return db.get_user(uid)

def verify_role_code(school_id,role,raw):
    r=db.get_role_code(school_id,role); return bool(r) and hmac.compare_digest(r['code_hash'],code_hash(raw))

def register_user(full_name,email,password,school_code,role,verification_code):
    if role not in ROLES: raise ValueError('Select a valid post held.')
    if db.get_user_by_email(str(email).strip().lower()): raise ValueError('An account already exists with that email.')
    school=db.get_school_by_code(str(school_code or '').strip().upper())
    if not school: raise ValueError('Institution code was not recognised.')
    if not verify_role_code(school['id'],role,verification_code): raise ValueError(f"The verification code for {role_label(role)} is incorrect.")
    return db.get_user(db.create_user(school['id'],str(full_name).strip(),str(email).strip().lower(),hash_password(password),role))

def reset_password(email,school_code,role,verification_code,new_password):
    u=db.get_user_by_email(email); school=db.get_school_by_code(str(school_code or '').strip().upper())
    if not u: raise ValueError('No account was found for that email.')
    if not school or u.get('school_id')!=school['id']: raise ValueError('Institution details do not match this account.')
    if u['role']!=role: raise ValueError('Selected post does not match this account.')
    if not verify_role_code(school['id'],role,verification_code): raise ValueError('Role verification code is incorrect.')
    db.update_user_password(u['id'],hash_password(new_password))

def change_role_code(user,role,new_code):
    if not can(user,'manage_users'): raise PermissionError('Not authorised.')
    if role not in ROLES: raise ValueError('Invalid role.')
    if len(str(new_code or '').strip())<6: raise ValueError('Use at least 6 characters.')
    db.set_role_code(user['school_id'],role,code_hash(new_code))

from __future__ import annotations

import json, html, re
from datetime import date, datetime, timedelta
from pathlib import Path
import pandas as pd
import streamlit as st

import database as db
import auth
import ml_engine
import ai_copilot

st.set_page_config(page_title='EduPulse | Academic Intelligence', page_icon=None, layout='wide', initial_sidebar_state='expanded')
db.init_db()

st.markdown('''
<style>
:root{--bg:#07111f;--panel:#0e1b2d;--line:#223753;--text:#eef6ff;--muted:#95a8c2;--accent:#52c8ff;--green:#45d69a;--amber:#f6c45f;--red:#ff6f7d}
.stApp{background:var(--bg);color:var(--text)}
[data-testid="stSidebar"]{background:#0a1728;border-right:1px solid var(--line)}
.block-container{max-width:1500px;padding-top:1.3rem;padding-bottom:4rem}
.hero{background:radial-gradient(circle at 90% 0%,rgba(82,200,255,.16),transparent 30%),linear-gradient(135deg,#10213a,#0b1728);border:1px solid var(--line);border-radius:22px;padding:28px;margin-bottom:20px}
.hero h1{margin:.15rem 0 .35rem;color:white;font-size:2.35rem;letter-spacing:-.04em}.hero p{color:var(--muted);max-width:900px;margin:0;line-height:1.55}
.kicker{font-size:.72rem;font-weight:800;letter-spacing:.12em;color:#a3e6ff;text-transform:uppercase}
.card{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:18px;margin-bottom:12px}
.risk-LOW{border-left:5px solid var(--green)}.risk-MODERATE{border-left:5px solid var(--amber)}.risk-HIGH{border-left:5px solid #ff9f5a}.risk-CRITICAL{border-left:5px solid var(--red)}
.chip{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:5px 10px;margin-right:6px;background:#142640;font-size:.75rem}
[data-testid="stMetric"]{background:var(--panel);border:1px solid var(--line);border-radius:15px;padding:15px}[data-testid="stMetricLabel"]{color:var(--muted)}[data-testid="stMetricValue"]{color:white}
.stButton>button,.stDownloadButton>button{border-radius:10px;min-height:2.65rem;border:1px solid var(--line)}
div[data-testid="stDataFrame"]{border:1px solid var(--line);border-radius:14px;overflow:hidden}
</style>
''', unsafe_allow_html=True)

def hero(title, subtitle, kicker='EDUPULSE'):
    st.markdown(f'<div class="hero"><div class="kicker">{html.escape(kicker)}</div><h1>{html.escape(title)}</h1><p>{html.escape(subtitle)}</p></div>', unsafe_allow_html=True)
def rerun(): st.rerun()
def school_for(user): return db.get_school(user['school_id']) if user and user.get('school_id') else None
def visible_students(user): return db.list_students_for_user(user, view_all=auth.can(user,'view_all_students'))
def student_label(s): return f"{s['student_id'] or 'ID'} — {s['full_name']}"
def cohort_label(s):
    records=db.list_semester_records(s['id'])
    if not records: return 'Fresher / Baseline'
    if int(s.get('level') or 100)>=int(s.get('programme_duration') or 4)*100: return 'Final Year'
    return 'Continuing'
def sf(v,d=None):
    try: return float(v) if v not in ('',None) else d
    except: return d
def si(v,d=None):
    try: return int(float(v)) if v not in ('',None) else d
    except: return d

def pred_db(student_id):
    row=db.latest_prediction(student_id)
    if not row: return None
    try: factors=json.loads(row.get('factors_json') or '[]')
    except: factors=[]
    return {'available':True,'model_version':row.get('model_version'),'predicted_next_gpa':row.get('predicted_next_gpa'),'predicted_final_cgpa':row.get('predicted_final_cgpa'),'risk_probability':float(row.get('risk_probability') or 0),'risk_level':row.get('risk_level'),'factors':factors,'recommendations':ml_engine.recommend_actions(row.get('risk_level'),factors)}

def automate(student_id,user):
    student=db.get_student(student_id); school=db.get_school(student['school_id']); records=db.list_semester_records(student_id)
    result=ml_engine.predict_student(records,student,float(school['gpa_scale']))
    if not result.get('available'): return result
    latest=db.latest_semester_record(student_id)
    pid=db.save_prediction(student_id,latest['id'] if latest else None,result,user['id'])
    if result['risk_level'] in {'MODERATE','HIGH','CRITICAL'}: db.upsert_watchlist(student['school_id'],student_id,pid,result['risk_level'],user['id'])
    else: db.resolve_watchlist(student['school_id'],student_id)
    db.log_action(user,'MODEL_REFRESH','student',student_id,f"{result['risk_level']} {result['risk_probability']:.4f}")
    return result

def risk_card(pred):
    if not pred: return
    level=pred['risk_level']
    st.markdown(f'<div class="card risk-{level}"><span class="chip">{level} RISK</span><span class="chip">{pred["risk_probability"]:.1%} probability</span><h3 style="margin:.8rem 0 .2rem">Academic Risk Intelligence</h3><span style="color:#95a8c2">Current model-generated predictive assessment.</span></div>', unsafe_allow_html=True)


def activity_description(item):
    action=(item.get('action') or '').upper()
    who=item.get('full_name') or 'An authorised user'
    details=(item.get('details') or '').strip()
    labels={
        'SAVE_STUDENT': 'registered or updated a student profile',
        'SAVE_ACADEMIC_RECORD': 'recorded a semester academic result',
        'MODEL_REFRESH': 'automatically refreshed a student prediction and risk status',
        'SAVE_INTERVENTION': 'created an academic support intervention',
        'SAVE_FOLLOWUP': 'recorded an intervention follow-up',
        'SCHOOL_SETTINGS': 'updated the institution settings',
        'LOGIN': 'signed in to EduPulse',
        'LOGOUT': 'signed out of EduPulse',
        'INITIAL_PRINCIPAL_SETUP': 'completed the initial institution setup',
    }
    text=labels.get(action, action.replace('_',' ').title().lower())
    if action=='MODEL_REFRESH' and details:
        bits=details.split()
        level=bits[0].title() if bits else 'Updated'
        text=f"EduPulse automatically reassessed a student and produced a {level} risk result"
    return f"{who} {text}."

def priority_explanation(row):
    level=str(row.get('risk_level') or 'UNKNOWN').title()
    prob=float(row.get('risk_probability') or 0)
    next_gpa=row.get('predicted_next_gpa')
    final_cgpa=row.get('predicted_final_cgpa')
    text=f"{row.get('full_name')} is currently in the {level} academic-risk category ({prob:.0%} estimated risk)."
    if next_gpa is not None:
        text+=f" EduPulse predicts the next GPA at about {float(next_gpa):.2f}."
    if final_cgpa is not None:
        text+=f" Predicted final CGPA is about {float(final_cgpa):.2f}."
    text+=" Review the student's academic history and decide whether an intervention is required."
    return text

def academic_history_frame(records):
    if not records:
        return pd.DataFrame()
    rf=pd.DataFrame(records).copy()
    wanted=['academic_session','level','semester','gpa','credits_registered','credits_earned','failed_courses','carryovers','attendance_rate']
    rf=rf[[c for c in wanted if c in rf.columns]]
    rf=rf.rename(columns={
        'academic_session':'Academic Session',
        'level':'Level',
        'semester':'Semester',
        'gpa':'Semester GPA',
        'credits_registered':'Credits Registered',
        'credits_earned':'Credits Earned',
        'failed_courses':'Failed Courses',
        'carryovers':'Carryovers',
        'attendance_rate':'Attendance %',
    })
    return rf

def level_summary(records, scale):
    if not records:
        return pd.DataFrame()
    rows=[]
    for level in sorted({int(r['level']) for r in records if r.get('level') is not None}):
        lr=[r for r in records if int(r.get('level') or 0)==level]
        gpas=[float(r['gpa']) for r in lr if r.get('gpa') is not None]
        rows.append({
            'Level':f'{level}L',
            'Semesters Recorded':len(gpas),
            f'Average GPA / {float(scale):.1f}':round(sum(gpas)/len(gpas),2) if gpas else None,
            'Failed Courses':sum(int(r.get('failed_courses') or 0) for r in lr),
            'Carryovers':sum(int(r.get('carryovers') or 0) for r in lr),
        })
    return pd.DataFrame(rows)

def automation_trace(result):
    if result and result.get('available'):
        st.info(
            f"Automated: CGPA recalculated → ML prediction refreshed → "
            f"{result.get('risk_level','Unknown').title()} risk checked → Early Warning updated → AI insight prepared."
        )

# AUTHENTICATION
if 'user' not in st.session_state:
    st.session_state.user=None
if 'pending_principal_signup' not in st.session_state:
    st.session_state.pending_principal_signup=None
if 'pending_role_signup' not in st.session_state:
    st.session_state.pending_role_signup=None

school_count=db.query_one('SELECT COUNT(*) AS n FROM schools')['n']

if st.session_state.user is None:
    hero('EduPulse','Academic intelligence, early warning and student intervention support.','WELCOME')

    mode=st.radio(
        'Account access',
        ['Login','Register'],
        horizontal=True,
        label_visibility='collapsed'
    )

    if mode=='Login':
        left,center,right=st.columns([1,1.15,1])
        with center:
            st.markdown('### Welcome back')
            with st.form('login'):
                email=st.text_input('Email')
                password=st.text_input('Password',type='password')
                submit=st.form_submit_button('Login',type='primary',use_container_width=True)
            if submit:
                u=auth.login(email,password)
                if not u:
                    st.error('Incorrect email/password or the account is inactive.')
                else:
                    st.session_state.user=u
                    db.log_action(u,'LOGIN')
                    rerun()

            with st.expander('Forgot password?'):
                st.caption('Verify your institution and post before setting a new password.')
                with st.form('forgot'):
                    f_email=st.text_input('Registered email')
                    f_school_code=st.text_input('Institution code')
                    f_role_label=st.selectbox('Post held',[auth.role_label(r) for r in auth.ROLES])
                    f_role={auth.role_label(r):r for r in auth.ROLES}[f_role_label]
                    f_verification=st.text_input(f'{f_role_label} verification code',type='password')
                    f_new_password=st.text_input('New password',type='password')
                    f_confirm=st.text_input('Confirm new password',type='password')
                    f_submit=st.form_submit_button('Reset password',use_container_width=True)

                if f_submit:
                    if school_count==0:
                        st.error('This institution has not been configured yet.')
                    elif f_new_password!=f_confirm:
                        st.error('Passwords do not match.')
                    elif len(f_new_password)<8:
                        st.error('Use at least 8 characters.')
                    else:
                        try:
                            auth.reset_password(f_email,f_school_code,f_role,f_verification,f_new_password)
                            st.success('Password changed successfully. You can now login.')
                        except Exception as e:
                            st.error(str(e))

    else:
        left,center,right=st.columns([.55,1.9,.55])
        with center:
            st.markdown('### Create your account')

            # Keep the post selector outside the form so Streamlit reruns immediately
            # when the user changes role. This updates the verification-code field
            # instead of leaving it stuck on Director.
            role_label=st.selectbox(
                'Post held',
                [auth.role_label(r) for r in auth.ROLES],
                key='registration_role'
            )
            role={auth.role_label(r):r for r in auth.ROLES}[role_label]

            with st.form('register'):
                a,b=st.columns(2)
                with a:
                    full_name=st.text_input('Full name')
                    email=st.text_input('Email')
                    school_code=st.text_input(
                        'Institution code',
                        disabled=(school_count==0 and role=='PRINCIPAL')
                    )
                with b:
                    password=st.text_input('Password',type='password')
                    confirm=st.text_input('Confirm password',type='password')
                    verification_code=st.text_input(
                        f'{role_label} verification code',
                        type='password'
                    )

                submit=st.form_submit_button('Create Account',type='primary',use_container_width=True)

            if submit:
                if not full_name.strip() or not email.strip():
                    st.error('Enter your full name and email.')
                elif password != confirm:
                    st.error('Passwords do not match.')
                elif len(password) < 8:
                    st.error('Password must contain at least 8 characters.')
                elif school_count == 0 and role != 'PRINCIPAL':
                    st.error('The Director must configure the institution first.')
                elif school_count == 0 and role == 'PRINCIPAL':
                    if verification_code.strip() != 'EDUPULSE-DIRECTOR-2026':
                        st.error('Director verification code is incorrect.')
                    else:
                        st.session_state.pending_principal_signup = {
                            'full_name':full_name.strip(),
                            'email':email.strip(),
                            'password':password
                        }
                        st.session_state.principal_verified=True
                        rerun()
                else:
                    try:
                        auth.register_user(
                            full_name.strip(),
                            email.strip(),
                            password,
                            school_code.strip(),
                            role,
                            verification_code
                        )
                        st.success(f'{role_label} account created. You can now login.')
                    except Exception as e:
                        st.error(str(e))

    # First Director: institution setup only AFTER Director account registration verification
    if st.session_state.get('principal_verified') and st.session_state.get('pending_principal_signup'):
        st.markdown("---")
        st.markdown("## Institution Configuration")
        st.caption("This is a one-time setup and can only be completed by the first Director.")
        p=st.session_state.pending_principal_signup

        with st.form('principal_institution_setup'):
            a,b=st.columns(2)
            with a:
                institution=st.text_input('Institution name')
                institution_code=st.text_input('Institution code',placeholder='SQI')
                scale=st.selectbox('Official GPA / CGPA scale',[5.0,4.0])
                duration=st.selectbox('Default course duration (years)',[4,5])
            with b:
                st.markdown('#### Role verification codes')
                st.caption('Set private verification codes for each authorised post. The Director or Rector can change them later from School Settings.')
                vpc=st.text_input('Rector code',type='password')
                ic=st.text_input('Intervention Committee code',type='password')
                ac=st.text_input('Academic Adviser code',type='password')
                lc=st.text_input('Lecturer code',type='password')
            finish=st.form_submit_button('Complete Setup and Create Director Account',type='primary',use_container_width=True)

        if finish:
            vals=[institution,institution_code,vpc,ic,ac,lc]
            if not all(str(x).strip() for x in vals):
                st.error('Complete every institution setup field.')
            else:
                try:
                    principal_role_code='EDUPULSE-DIRECTOR-2026'
                    owner=auth.setup_institution(
                        institution,institution_code,scale,duration,
                        p['full_name'],p['email'],p['password'],
                        {
                            'PRINCIPAL':principal_role_code,
                            'VICE_PRINCIPAL':vpc,
                            'INTERVENTION_COMMITTEE':ic,
                            'ACADEMIC_ADVISER':ac,
                            'LECTURER':lc,
                        }
                    )
                    # Convert initial system owner into the actual Director account.
                    db.update_user_role(owner['id'],'PRINCIPAL')
                    st.session_state.user=db.get_user(owner['id'])
                    st.session_state.pending_principal_signup=None
                    st.session_state.principal_verified=False
                    db.log_action(st.session_state.user,'INITIAL_PRINCIPAL_SETUP')
                    rerun()
                except Exception as e:
                    st.error(str(e))

    st.stop()

user=db.get_user(st.session_state.user['id']); st.session_state.user=user; school=school_for(user)

ADMIN_ROLES={'PRINCIPAL','VICE_PRINCIPAL','INTERVENTION_COMMITTEE','ACADEMIC_ADVISER','SUPERADMIN'}
TOP_TIER_ROLES={'PRINCIPAL','VICE_PRINCIPAL','SUPERADMIN'}
is_admin=user['role'] in ADMIN_ROLES
is_top_tier=user['role'] in TOP_TIER_ROLES
can_view_settings=auth.can(user,'view_school_settings')
if 'page' not in st.session_state: st.session_state.page='Overview' if is_admin else 'Student Registry'

# NAVIGATION
with st.sidebar:
    st.markdown("<div style='font-size:1.6rem;font-weight:850;color:white'>Edu<span style='color:#52c8ff'>Pulse</span></div>",unsafe_allow_html=True)
    st.caption('Academic Intelligence & Early Warning'); st.markdown(f"**{user['full_name']}**"); st.caption(auth.role_label(user['role'])); st.caption(school['name']); st.divider()
    pages=[]
    if is_admin:
        pages.append('Overview')
    if auth.can(user,'register_students'): pages.append('Student Registry')
    if auth.can(user,'record_results'): pages.append('Academic Records')
    if auth.can(user,'assess_students'): pages.append('Student Intelligence')
    if auth.can(user,'bulk_screen'): pages.append('Bulk Screening')
    if auth.can(user,'view_watchlist'): pages.append('Early Warning Center')
    if auth.can(user,'manage_interventions'): pages.append('Intervention Management')
    if auth.can(user,'followup'): pages.append('Follow-up Monitoring')
    if auth.can(user,'ai_copilot'): pages.append('Academic Copilot')
    if auth.can(user,'reports'): pages.append('Reports')
    # Keep School Settings as a separate admin page without allowing
    # the main navigation radio to overwrite it on the rerun triggered by the button.
    settings_open = can_view_settings and st.session_state.page == 'School Settings'

    if settings_open:
        cur=st.radio('Navigation',pages,index=None,label_visibility='collapsed',key='main_nav_from_settings')
        if cur:
            st.session_state.page=cur
            rerun()
    else:
        cur=st.radio(
            'Navigation',pages,
            index=pages.index(st.session_state.page) if st.session_state.page in pages else 0,
            label_visibility='collapsed',
            key='main_nav'
        )
        st.session_state.page=cur

    st.divider(); st.markdown('##### More')
    if can_view_settings and st.button('School Settings',use_container_width=True):
        st.session_state.page='School Settings'
        # Clear any temporary navigation selection used while Settings is open.
        st.session_state.pop('main_nav_from_settings',None)
        rerun()
    if st.button('Sign out',use_container_width=True):
        db.log_action(user,'LOGOUT')
        st.session_state.user=None
        rerun()
page=st.session_state.page

# OVERVIEW
if page=='Overview':
    hero(
        f"Welcome back, {user['full_name'].split()[0]}",
        "Student performance, risk and support at a glance."
    )
    counts=db.school_counts(user['school_id'])
    c1,c2,c3,c4=st.columns(4)
    c1.metric('Registered Students',counts['students'])
    c2.metric('Students Needing Attention',counts['watchlist'])
    c3.metric('Interventions Created',counts['interventions'])
    c4.metric('Follow-ups Recorded',counts['followups'])

    students=visible_students(user)
    stages=[cohort_label(s) for s in students]
    freshers=stages.count('Fresher / Baseline')
    continuing=stages.count('Continuing')
    final_year=stages.count('Final Year')

    st.markdown('### Student stage at a glance')
    a,b,c=st.columns(3)
    a.metric('Fresher / Baseline',freshers)
    b.metric('Continuing Students',continuing)
    c.metric('Final-Year Students',final_year)

    left,right=st.columns([1.05,1])
    with left:
        st.markdown('### Recent Activity')
        activity=db.recent_audit(user['school_id'],8)
        if activity:
            for item in activity:
                when=str(item.get('created_at') or '').replace('T',' ')[:16]
                st.markdown(f"**{when}** — {activity_description(item)}")
        else:
            st.info('No activity has been recorded yet.')

    with right:
        st.markdown('### Priority Attention')
        st.caption('Students automatically flagged for academic review.')
        watch=db.list_watchlist(user['school_id'])
        if watch:
            for row in watch[:6]:
                level=str(row.get('risk_level') or 'UNKNOWN')
                st.markdown(
                    f'<div class="card risk-{level}"><b>{html.escape(row.get("full_name") or "Student")}</b>'
                    f'<br><span style="color:#95a8c2">{html.escape(priority_explanation(row))}</span></div>',
                    unsafe_allow_html=True
                )
        else:
            st.success('No student currently has an open Moderate, High or Critical early-warning case.')


# STUDENT REGISTRY
elif page=='Student Registry':
    hero('Student Registry','Register students and maintain the information EduPulse needs for academic monitoring.')
    t1,t2=st.tabs(['Register / Update Student','Student List'])
    with t1:
        with st.form('student'):
            a,b,c=st.columns(3)
            with a:
                student_id=st.text_input('Student ID *')
                full_name=st.text_input('Student full name *')
                gender=st.selectbox('Gender',['Not specified','Female','Male','Other'])
                course=st.text_input('Course *')
            with b:
                department=st.text_input('Department')
                level=st.selectbox('Current level',[100,200,300,400,500])
                allowed_duration=[4,5]
                default_duration=int(school['default_programme_duration'])
                duration=st.selectbox(
                    'Course duration (years)',
                    allowed_duration,
                    index=allowed_duration.index(default_duration) if default_duration in allowed_duration else 0
                )
                academic_session=st.text_input('Academic session',value=school.get('current_academic_session') or '2025/2026')
            with c:
                entry_year=st.number_input('Entry year',2000,2040,2025)
                study_mode=st.selectbox('Study mode',['Full-time','Part-time','Distance','Other'])
                student_email=st.text_input('Student email')
                parent_name=st.text_input('Parent / guardian name')
                parent_email=st.text_input('Parent / guardian email')
                guardian_phone=st.text_input('Parent / guardian phone')
            submit=st.form_submit_button('Save Student',type='primary')
        if submit:
            if not student_id.strip() or not full_name.strip() or not course.strip():
                st.error('Student ID, name and course are required.')
            elif int(level) > int(duration)*100:
                st.error(f'A {duration}-year course cannot have a current level above {duration*100}L.')
            else:
                sid=db.upsert_student({
                    'student_id':student_id.strip(),
                    'matric_no':student_id.strip(),
                    'full_name':full_name.strip(),
                    'gender':None if gender=='Not specified' else gender,
                    'programme':course.strip(),
                    'department':department or None,
                    'faculty':None,
                    'level':level,
                    'programme_duration':duration,
                    'academic_session':academic_session,
                    'entry_year':int(entry_year),
                    'study_mode':study_mode,
                    'student_email':student_email or None,
                    'parent_name':parent_name or None,
                    'parent_email':parent_email or None,
                    'guardian_phone':guardian_phone or None,
                    'admission_score':None
                },user['id'],user['school_id'])
                db.log_action(user,'SAVE_STUDENT','student',sid)
                st.success('Student profile saved.')

    with t2:
        rows=visible_students(user)
        if rows:
            f=pd.DataFrame(rows)
            f['stage']=[cohort_label(r) for r in rows]
            f=f.rename(columns={
                'student_id':'Student ID','full_name':'Name','gender':'Gender',
                'programme':'Course','department':'Department','level':'Level',
                'academic_session':'Academic Session','stage':'Student Stage'
            })
            st.dataframe(
                f[['Student ID','Name','Gender','Course','Department','Level','Academic Session','Student Stage']],
                hide_index=True,use_container_width=True
            )
        else:
            st.info('No students are visible to this account.')

# ACADEMIC RECORDS
elif page=='Academic Records':
    hero(
        'Academic Records',
        'Build the student’s academic history semester by semester. EduPulse uses the complete available history—from earlier levels to the current level—when refreshing predictions.'
    )
    students=visible_students(user)
    if not students:
        st.info('Register a student first.')
        st.stop()

    smap={student_label(s):s for s in students}
    student=smap[st.selectbox('Student',list(smap))]
    records=db.list_semester_records(student['id'])
    trajectory=ml_engine.trajectory_summary(records,school['gpa_scale'])

    c1,c2,c3,c4=st.columns(4)
    c1.metric('Institution GPA Scale',f"{float(school['gpa_scale']):.1f}")
    c2.metric('Current CGPA',f"{ml_engine.calculate_cgpa(records):.2f}/{float(school['gpa_scale']):.1f}" if records else '—')
    c3.metric('Student Stage',cohort_label(student))
    c4.metric('Performance Direction',trajectory['direction'].title())

    st.caption("Enter earlier results too. EduPulse analyses the full available history.")

    with st.form('record'):
        a,b,c=st.columns(3)
        with a:
            session=st.text_input('Academic session',value=student.get('academic_session') or school.get('current_academic_session') or '2025/2026')
            valid_levels=[100,200,300,400,500]
            current_level=int(student['level'])
            level=st.selectbox(
                'Level',
                valid_levels,
                index=valid_levels.index(current_level) if current_level in valid_levels else 0
            )
            semester=st.selectbox('Semester',['First','Second'])
        with b:
            gpa=st.number_input(
                f"Semester GPA (0.00–{float(school['gpa_scale']):.1f}) *",
                0.0,float(school['gpa_scale']),
                min(3.0,float(school['gpa_scale'])),0.01
            )
            cr=st.number_input('Credit units registered',0.0,40.0,20.0,1.0)
            ce=st.number_input('Credit units earned',0.0,40.0,18.0,1.0)
        with c:
            failed=st.number_input('Failed courses',0,20,0)
            carry=st.number_input('Carryovers',0,20,0)
            attendance=st.number_input('Attendance rate %',0.0,100.0,80.0,1.0)
        submit=st.form_submit_button('Save Academic Record',type='primary')

    if submit:
        if int(level) > int(student.get('programme_duration') or school['default_programme_duration'])*100:
            st.error('The selected level is above this student’s configured course duration.')
        elif ce > cr and cr > 0:
            st.error('Credit units earned cannot be greater than credit units registered.')
        else:
            rid=db.add_semester_record(
                student['id'],
                {
                    'academic_session':session,
                    'level':level,
                    'semester':semester,
                    'gpa':gpa,
                    'credits_registered':cr or None,
                    'credits_earned':ce or None,
                    'failed_courses':failed,
                    'carryovers':carry,
                    'attendance_rate':attendance if attendance>0 else None,
                    'ca_average':None,
                    'exam_average':None
                },
                user['id']
            )
            db.log_action(user,'SAVE_ACADEMIC_RECORD','semester_record',rid)
            result=automate(student['id'],user)
            st.success('Academic record saved. EduPulse automatically refreshed the student’s academic intelligence.')
            automation_trace(result)
            if result.get('available'):
                risk_card(result)
                st.markdown('### Academic Insight')
                context=ai_copilot.build_context(
                    student,db.list_semester_records(student['id']),result,
                    db.list_interventions(user['school_id'],student['id']),
                    db.list_followups(user['school_id'],student['id']),
                    school['gpa_scale']
                )
                st.write(ai_copilot.local_brief(context))

    records=db.list_semester_records(student['id'])
    if records:
        st.markdown('### Academic journey by level')
        st.caption("Level-by-level performance summary.")
        st.dataframe(level_summary(records,school['gpa_scale']),hide_index=True,use_container_width=True)

        st.markdown('### Semester-by-semester academic history')
        st.dataframe(academic_history_frame(records),hide_index=True,use_container_width=True)

        rf=pd.DataFrame(records)
        rf['Academic Period']=rf['academic_session'].astype(str)+' • '+rf['level'].astype(str)+'L • '+rf['semester']
        st.line_chart(rf.set_index('Academic Period')['gpa'])
        traj=ml_engine.trajectory_summary(records,school['gpa_scale'])
        st.info(traj['message'])
    else:
        st.info('No semester result has been recorded yet. The student remains in Baseline monitoring.')

# STUDENT INTELLIGENCE
elif page=='Student Intelligence':
    hero(
        'Student Intelligence',
        'Understand how the student has performed over time, what EduPulse predicts next, why the prediction was produced, and what academic support may help.'
    )
    students=visible_students(user)
    if not students:
        st.info('No student is available.')
        st.stop()

    labels={student_label(s):s for s in students}
    student=labels[st.selectbox('Student',list(labels))]
    records=db.list_semester_records(student['id'])

    if not records:
        st.markdown('### Academic Baseline Profile')
        a,b,c=st.columns(3)
        a.metric('Current Level',f"{student['level']}L")
        b.metric('Course',student['programme'])
        c.metric('Monitoring Status','Fresher / Baseline')
        st.markdown('### Academic Insight')
        st.write(ai_copilot.local_brief(ai_copilot.build_context(student,[],None,[],[],school['gpa_scale'])))
        st.stop()

    pred=automate(student['id'],user)
    pred=pred if pred.get('available') else pred_db(student['id'])
    current=ml_engine.calculate_cgpa(records)
    traj=ml_engine.trajectory_summary(records,school['gpa_scale'])

    risk_card(pred)
    a,b,c,d=st.columns(4)
    a.metric('Latest GPA',f"{float(records[-1]['gpa']):.2f}/{float(school['gpa_scale']):.1f}")
    b.metric('Current CGPA',f"{current:.2f}/{float(school['gpa_scale']):.1f}")
    c.metric('Predicted Next GPA',f"{pred['predicted_next_gpa']:.2f}/{float(school['gpa_scale']):.1f}" if pred.get('predicted_next_gpa') is not None else '—')
    d.metric('Predicted Final CGPA',f"{pred['predicted_final_cgpa']:.2f}/{float(school['gpa_scale']):.1f}")

    st.markdown('### Academic journey considered by EduPulse')
    st.caption("Prediction uses the student's complete recorded academic history.")
    st.dataframe(level_summary(records,school['gpa_scale']),hide_index=True,use_container_width=True)
    st.info(traj['message'])

    st.markdown('### Why this prediction?')
    factors=[x for x in (pred.get('factors') or []) if isinstance(x,str) and x.strip()]
    if factors:
        for x in factors:
            st.markdown(f"- {x}")
    else:
        st.write("EduPulse combined the student's full recorded academic history; no single issue dominated the prediction.")

    st.markdown('### Risk evidence in the student’s records')
    latest=records[-1]
    evidence=[]
    if len(records)>=2:
        first=float(records[0]['gpa'])
        last=float(latest['gpa'])
        evidence.append(f"Recorded GPA changed from {first:.2f}/{float(school['gpa_scale']):.1f} in the earliest available semester to {last:.2f}/{float(school['gpa_scale']):.1f} in the latest.")
    if int(latest.get('failed_courses') or 0)>0:
        evidence.append(f"The latest record contains {int(latest.get('failed_courses') or 0)} failed course(s).")
    if int(latest.get('carryovers') or 0)>0:
        evidence.append(f"The latest record contains {int(latest.get('carryovers') or 0)} carryover(s).")
    if latest.get('attendance_rate') is not None:
        evidence.append(f"Latest recorded attendance is {float(latest['attendance_rate']):.0f}%.")
    for item in evidence or ["No additional warning evidence is recorded beyond the GPA trajectory and model assessment."]:
        st.markdown(f"- {item}")

    st.markdown('### Recommended academic support')
    for x in [x for x in (pred.get('recommendations') or []) if isinstance(x,str) and x.strip()]:
        st.markdown(f"- {x}")

    st.markdown('### Academic Insight')
    context=ai_copilot.build_context(
        student,records,pred,
        db.list_interventions(user['school_id'],student['id']),
        db.list_followups(user['school_id'],student['id']),
        school['gpa_scale']
    )
    st.write(ai_copilot.local_brief(context))

    with st.expander('See semester-by-semester results'):
        st.dataframe(academic_history_frame(records),hide_index=True,use_container_width=True)

# BULK SCREENING
elif page=='Bulk Screening':
    hero(
        'Bulk Screening',
        'Screen many student semester records at once. EduPulse validates the upload, stores each academic history, runs ML assessment and automatically updates Early Warning.'
    )
    test_path=Path(__file__).resolve().parent/'test_bulk_screening.csv'
    if test_path.exists():
        st.download_button('Download Test CSV',test_path.read_bytes(),'test_bulk_screening.csv','text/csv')

    uploaded=st.file_uploader('Upload CSV',type=['csv'])
    if uploaded:
        incoming=pd.read_csv(uploaded)
        st.success(f'File loaded: {len(incoming)} semester record(s).')
        st.dataframe(incoming.head(15),hide_index=True,use_container_width=True)

        aliases={
            'student_id':['studentid','student_id','id','registrationnumber','regno'],
            'full_name':['studentname','fullname','name'],
            'programme':['course','programme','program'],
            'level':['level','studentlevel'],
            'academic_session':['session','academicsession'],
            'semester':['semester','term'],
            'gpa':['gpa','semestergpa','semester_gpa'],
            'credits_registered':['creditunits','creditsregistered','registeredcredits'],
            'credits_earned':['creditsearned','earnedcredits'],
            'failed_courses':['failedcourses','failed_courses','fails'],
            'carryovers':['carryovers','carryover'],
            'attendance_rate':['attendance','attendancerate','attendance_rate'],
            'parent_email':['parentemail','guardianemail'],
            'gender':['gender','sex']
        }
        norm=lambda x: re.sub(r'[^a-z0-9]+','',str(x).lower())
        ncols={norm(c):c for c in incoming.columns}
        guessed={k:next((ncols[norm(n)] for n in names if norm(n) in ncols),None) for k,names in aliases.items()}
        required=['student_id','full_name','programme','level','academic_session','semester','gpa']

        st.markdown('### Confirm what each uploaded column means')
        options=['-- Not provided --']+list(incoming.columns)
        mapping={}
        cols=st.columns(2)
        targets=required+[k for k in aliases if k not in required]
        for i,t in enumerate(targets):
            d=guessed.get(t)
            idx=options.index(d) if d in options else 0
            with cols[i%2]:
                mapping[t]=st.selectbox(
                    t.replace('_',' ').title()+(' *' if t in required else ''),
                    options,index=idx,key='map_'+t
                )

        missing=[r for r in required if mapping[r]=='-- Not provided --']
        if missing:
            st.warning('Please identify these required fields: '+', '.join(x.replace('_',' ') for x in missing))
        else:
            bad_gpa=pd.to_numeric(incoming[mapping['gpa']],errors='coerce')
            bad_level=pd.to_numeric(incoming[mapping['level']],errors='coerce')
            validation=[]
            if bad_gpa.isna().any():
                validation.append('Some GPA values are not numeric.')
            if (bad_gpa > float(school['gpa_scale'])).any() or (bad_gpa < 0).any():
                validation.append(f"Some GPA values fall outside the institution's 0–{float(school['gpa_scale']):.1f} scale.")
            if bad_level.isna().any() or (~bad_level.isin([100,200,300,400,500])).any():
                validation.append('Levels must be 100, 200, 300, 400 or 500.')

            if validation:
                for v in validation:
                    st.error(v)
            elif st.button('Start Bulk Screening',type='primary'):
                progress=st.progress(0)
                status=st.empty()
                status.info('Step 1 of 5 — Validating and preparing uploaded records...')
                progress.progress(10)

                unique_ids=incoming[mapping['student_id']].astype(str).drop_duplicates().tolist()
                status.info('Step 2 of 5 — Saving student profiles and semester histories...')
                for idx,(_,row) in enumerate(incoming.iterrows(),start=1):
                    d={k:(row[v] if v!='-- Not provided --' else None) for k,v in mapping.items()}
                    sid=db.upsert_student({
                        'student_id':str(d['student_id']),
                        'matric_no':str(d['student_id']),
                        'full_name':str(d['full_name']),
                        'programme':str(d['programme']),
                        'gender':d.get('gender'),
                        'department':None,'faculty':None,
                        'level':si(d['level'],100),
                        'programme_duration':int(school['default_programme_duration']),
                        'academic_session':str(d['academic_session']),
                        'student_email':None,'parent_email':d.get('parent_email'),
                        'parent_name':None,'guardian_phone':None,'entry_year':None,
                        'study_mode':'Full-time','admission_score':None
                    },user['id'],user['school_id'])
                    sem='Second' if str(d['semester']).strip().lower() in {'2','second','second semester'} else 'First'
                    db.add_semester_record(sid,{
                        'academic_session':str(d['academic_session']),
                        'level':si(d['level'],100),
                        'semester':sem,
                        'gpa':sf(d['gpa'],0),
                        'credits_registered':sf(d.get('credits_registered')),
                        'credits_earned':sf(d.get('credits_earned')),
                        'failed_courses':si(d.get('failed_courses'),0),
                        'carryovers':si(d.get('carryovers'),0),
                        'attendance_rate':sf(d.get('attendance_rate')),
                        'ca_average':None,'exam_average':None
                    },user['id'])
                    progress.progress(min(55,10+int(45*idx/max(1,len(incoming)))))

                status.info('Step 3 of 5 — Analysing each student’s complete academic trajectory...')
                out=[]
                for idx,sidv in enumerate(unique_ids,start=1):
                    stu=db.query_one(
                        'SELECT * FROM students WHERE school_id=? AND student_id=?',
                        (user['school_id'],sidv)
                    )
                    result=automate(stu['id'],user)
                    out.append({
                        'Student ID':sidv,
                        'Student Name':stu['full_name'],
                        'Risk Level':result.get('risk_level','BASELINE'),
                        'Risk Probability':result.get('risk_probability'),
                        f'Predicted Next GPA / {float(school["gpa_scale"]):.1f}':result.get('predicted_next_gpa'),
                        f'Predicted Final CGPA / {float(school["gpa_scale"]):.1f}':result.get('predicted_final_cgpa')
                    })
                    progress.progress(min(85,55+int(30*idx/max(1,len(unique_ids)))))

                status.info('Step 4 of 5 — Updating Early Warning cases for Moderate, High and Critical risk...')
                progress.progress(95)
                st.session_state.bulk_result=pd.DataFrame(out)
                db.log_action(user,'BULK_SCREEN','cohort',None,f'{len(unique_ids)} students')
                status.success('Step 5 of 5 — Screening complete. Predictions, AI-ready evidence and Early Warning status have been updated.')
                progress.progress(100)

    if 'bulk_result' in st.session_state:
        result_df=st.session_state.bulk_result
        st.markdown('### Screening summary')
        total=len(result_df)
        risk_counts=result_df['Risk Level'].value_counts() if 'Risk Level' in result_df.columns else pd.Series(dtype=int)
        a,b,c,d=st.columns(4)
        a.metric('Students Screened',total)
        b.metric('Low Risk',int(risk_counts.get('LOW',0)))
        c.metric('Moderate / High',int(risk_counts.get('MODERATE',0)+risk_counts.get('HIGH',0)))
        d.metric('Critical',int(risk_counts.get('CRITICAL',0)))
        st.dataframe(result_df,hide_index=True,use_container_width=True)
        st.download_button(
            'Download Screening Result',
            result_df.to_csv(index=False).encode(),
            'screening_result.csv','text/csv'
        )
        st.caption('Automation completed: each screened student now has refreshed ML intelligence, and qualifying risk cases were automatically routed to Early Warning.')

# EARLY WARNING
elif page=='Early Warning Center':
    if not is_admin:
        st.error('This area is restricted to authorised academic administrators.')
        st.stop()

    hero('Early Warning Center','Students currently requiring academic attention.')
    watch=db.list_watchlist(user['school_id'])

    if not watch:
        st.success('No open early-warning case.')
        st.stop()

    wf=pd.DataFrame(watch).rename(columns={
        'full_name':'Student','programme':'Course','level':'Level','risk_level':'Risk',
        'risk_probability':'Estimated Risk','predicted_next_gpa':'Predicted Next GPA',
        'predicted_final_cgpa':'Predicted Final CGPA','status':'Case Status'
    })
    show=[c for c in ['Student','Course','Level','Risk','Estimated Risk','Predicted Next GPA','Predicted Final CGPA','Case Status'] if c in wf.columns]
    st.dataframe(wf[show],hide_index=True,use_container_width=True)

    labels={f"{r['full_name']} — {r['risk_level']}":r for r in watch}
    case=labels[st.selectbox('Review student',list(labels))]
    student=db.get_student(case['student_id'])
    records=db.list_semester_records(student['id'])

    risk_card({'risk_level':case['risk_level'],'risk_probability':float(case['risk_probability'])})

    st.markdown('### Why the student was flagged')
    if records:
        first=float(records[0]['gpa'])
        latest=float(records[-1]['gpa'])
        scale=float(school['gpa_scale'])
        if len(records)>1:
            if latest < first - (scale*0.05):
                st.write(f"The student's GPA has fallen from {first:.2f} to {latest:.2f} across the recorded semesters.")
            elif latest > first + (scale*0.05):
                st.write(f"The student's GPA has improved from {first:.2f} to {latest:.2f}, but the overall model still detected risk from the complete record.")
            else:
                st.write(f"The student's GPA has remained around {latest:.2f}, but the complete academic record still triggered the warning.")
        else:
            st.write(f"The current recorded GPA is {latest:.2f}/{scale:.1f} and the model identified a need for academic review.")

        latest_row=records[-1]
        extra=[]
        if int(latest_row.get('failed_courses') or 0)>0:
            extra.append(f"{int(latest_row.get('failed_courses') or 0)} failed course(s)")
        if int(latest_row.get('carryovers') or 0)>0:
            extra.append(f"{int(latest_row.get('carryovers') or 0)} carryover(s)")
        if latest_row.get('attendance_rate') is not None and float(latest_row['attendance_rate']) < 70:
            extra.append(f"{float(latest_row['attendance_rate']):.0f}% attendance")
        if extra:
            st.write("Latest record: " + ", ".join(extra) + ".")

        st.markdown('### Semester Results')
        st.dataframe(academic_history_frame(records),hide_index=True,use_container_width=True)
    else:
        st.write('No semester history is available for this warning.')

    st.markdown('### Recommended Support')
    pred=pred_db(student['id']) or {}
    recommendations=[r for r in pred.get('recommendations',[]) if isinstance(r,str) and r.strip()]
    for rec in recommendations[:4]:
        st.markdown(f"- {rec}")


# INTERVENTION
elif page=='Intervention Management':
    if not is_admin:
        st.error('This area is restricted to authorised academic administrators.')
        st.stop()
    hero(
        'Intervention Management',
        'Turn an Early Warning into a documented academic support plan, assign responsibility and set a review date.'
    )
    watch=db.list_watchlist(user['school_id'])
    if not watch:
        st.info('There is no open Early Warning case requiring an intervention at the moment.')
        st.stop()

    cmap={f"{r['full_name']} — {r['risk_level']}":r for r in watch}
    case=cmap[st.selectbox('Student case',list(cmap))]
    student=db.get_student(case['student_id'])
    pred=pred_db(student['id']) or {}

    st.markdown('### EduPulse support guidance')
    st.caption('These are model-informed academic support suggestions. The authorised academic team chooses the actual intervention.')
    for rec in pred.get('recommendations',[]):
        if isinstance(rec,str) and rec.strip():
            st.markdown(f"- {rec}")

    users=[u for u in db.list_users(user['school_id']) if u.get('active')]
    sm={f"{u['full_name']} — {auth.role_label(u['role'])}":u['id'] for u in users}

    with st.form('intervention'):
        a,b=st.columns(2)
        with a:
            itype=st.selectbox('Intervention type',[
                'Academic adviser consultation',
                'Targeted tutorial / remedial support',
                'Structured academic support plan',
                'Attendance / engagement review',
                'Course performance review',
                'Referral for further student support',
                'Other'
            ])
            owner=st.selectbox('Responsible officer',list(sm))
            status=st.selectbox('Intervention status',['PLANNED','INITIATED','ONGOING','COMPLETED'])
        with b:
            start=st.date_input('Start date',date.today())
            review=st.date_input('Review date',date.today()+timedelta(days=14))
            notify=st.checkbox('Prepare parent / guardian email notification')
        plan=st.text_area('Support plan',placeholder='Describe what support will be provided, how often, and what improvement is expected.')
        note=st.text_area('Academic adviser / committee note',placeholder='Record the reason for selecting this intervention and any relevant academic observation.')
        submit=st.form_submit_button('Save Intervention',type='primary')

    if submit:
        if review < start:
            st.error('Review date cannot be earlier than the intervention start date.')
        elif not plan.strip():
            st.error('Add a short support plan so the intervention can be followed up properly.')
        else:
            iid=db.add_intervention({
                'school_id':user['school_id'],'student_id':student['id'],
                'watchlist_id':case['id'],'intervention_type':itype,
                'description':plan,'owner_user_id':sm[owner],'status':status,
                'start_date':str(start),'review_date':str(review),
                'adviser_note':note,'parent_notification':notify,'created_by':user['id']
            })
            if notify and student.get('parent_email'):
                db.add_notification({
                    'school_id':user['school_id'],'student_id':student['id'],
                    'recipient_type':'PARENT_GUARDIAN','recipient_email':student['parent_email'],
                    'subject':f"Academic support update for {student['full_name']}",
                    'body':f"An academic support intervention has been recorded for {student['full_name']}. Intervention: {itype}. Review date: {review}.",
                    'created_by':user['id']
                })
            db.log_action(user,'SAVE_INTERVENTION','intervention',iid)
            st.success('Intervention saved. The case is now ready for follow-up monitoring.')

    hist=db.list_interventions(user['school_id'],student['id'])
    if hist:
        st.markdown('### Intervention history')
        hf=pd.DataFrame(hist).rename(columns={
            'intervention_type':'Intervention','owner_name':'Responsible Officer',
            'status':'Status','start_date':'Start Date','review_date':'Review Date',
            'description':'Support Plan','adviser_note':'Academic Note'
        })
        cols=[c for c in ['Intervention','Responsible Officer','Status','Start Date','Review Date','Support Plan','Academic Note'] if c in hf.columns]
        st.dataframe(hf[cols],hide_index=True,use_container_width=True)

# FOLLOW-UP
elif page=='Follow-up Monitoring':
    if not is_admin:
        st.error('This area is restricted to authorised academic administrators.')
        st.stop()
    hero(
        'Follow-up Monitoring',
        'Check whether academic support is helping. Compare the latest observed performance with the student’s previous academic position and record the outcome.'
    )
    ints=db.list_interventions(user['school_id'])
    if not ints:
        st.info('Follow-up Monitoring becomes available after an intervention has been created.')
        st.stop()

    imap={f"{i['full_name']} — {i['intervention_type']} — {i['status']}":i for i in ints}
    inter=imap[st.selectbox('Intervention',list(imap))]
    student=db.get_student(inter['student_id'])
    records=db.list_semester_records(student['id'])
    academic_cgpa=float(ml_engine.calculate_cgpa(records) or 0)
    existing=db.list_followups(user['school_id'],student['id'])
    comparison_cgpa=float(existing[0]['observed_cgpa']) if existing and existing[0].get('observed_cgpa') is not None else academic_cgpa

    st.markdown('### What is being compared')
    st.caption(
        f"EduPulse will compare this follow-up with the previous reference CGPA of {comparison_cgpa:.2f}/{float(school['gpa_scale']):.1f}. "
        "A clear rise is marked Improving, a clear fall is Declining, and a small change is Stable."
    )

    with st.form('follow'):
        a,b=st.columns(2)
        with a:
            obs=st.date_input('Observation date',date.today())
            ogpa=st.number_input(
                'Latest semester GPA',
                0.0,float(school['gpa_scale']),
                float(records[-1]['gpa']) if records else 0.0,0.01
            )
            ocgpa=st.number_input(
                'Current CGPA',
                0.0,float(school['gpa_scale']),
                academic_cgpa,0.01
            )
        with b:
            attendance=st.number_input('Current attendance %',0.0,100.0,80.0,1.0)
            delta=ocgpa-comparison_cgpa
            threshold=max(0.10,float(school['gpa_scale'])*0.03)
            progress='IMPROVING' if delta>=threshold else 'DECLINING' if delta<=-threshold else 'STABLE'
            st.metric('Calculated Progress Status',progress,delta=f"{delta:+.2f} CGPA")
        note=st.text_area('Follow-up observation',placeholder='What has changed since the intervention? Is the student attending support sessions? Are results or engagement improving?')
        submit=st.form_submit_button('Save Follow-up',type='primary')

    if submit:
        fid=db.add_followup({
            'school_id':user['school_id'],'student_id':student['id'],
            'intervention_id':inter['id'],'observation_date':str(obs),
            'observed_gpa':ogpa,'observed_cgpa':ocgpa,
            'attendance_rate':attendance,'progress_status':progress,
            'observation_note':note,'recorded_by':user['id']
        })
        db.log_action(user,'SAVE_FOLLOWUP','followup',fid)
        st.success(f'Follow-up saved. Current progress is classified as {progress.title()}.')

    follows=db.list_followups(user['school_id'],student['id'])
    if follows:
        st.markdown('### Follow-up history')
        ff=pd.DataFrame(follows).rename(columns={
            'observation_date':'Observation Date','observed_gpa':'Observed GPA',
            'observed_cgpa':'Observed CGPA','attendance_rate':'Attendance %',
            'progress_status':'Progress','observation_note':'Observation',
            'intervention_type':'Intervention'
        })
        cols=[c for c in ['Observation Date','Intervention','Observed GPA','Observed CGPA','Attendance %','Progress','Observation'] if c in ff.columns]
        st.dataframe(ff[cols],hide_index=True,use_container_width=True)
        st.download_button(
            'Download Follow-up Records',
            ff[cols].to_csv(index=False).encode(),
            f"{student['student_id']}_followup.csv",'text/csv'
        )

# COPILOT
elif page=='Academic Copilot':
    hero(
        'Academic Copilot',
        'Ask project-specific questions about a student’s recorded academic history, ML prediction, intervention and follow-up progress.'
    )
    students=visible_students(user)
    if not students:
        st.info('No student is available.')
        st.stop()

    smap={student_label(s):s for s in students}
    student=smap[st.selectbox('Student',list(smap))]
    records=db.list_semester_records(student['id'])
    pred=pred_db(student['id'])
    if records:
        fresh=automate(student['id'],user)
        if fresh.get('available'):
            pred=fresh

    context=ai_copilot.build_context(
        student,records,pred,
        db.list_interventions(user['school_id'],student['id']),
        db.list_followups(user['school_id'],student['id']),
        school['gpa_scale']
    )
    st.markdown('### AI Student Brief')
    st.write(ai_copilot.local_brief(context))

    st.markdown('### Quick academic questions')
    quick=st.radio(
        'Choose a question',
        [
            'Why is this student at risk?',
            'Explain the student’s academic history.',
            'What support should we prioritise?',
            'What is the predicted next GPA and final CGPA?',
            'Has the student improved?'
        ],
        label_visibility='collapsed'
    )
    if st.button('Ask Quick Question',type='primary'):
        st.markdown('### Copilot Response')
        st.write(ai_copilot.answer(quick,context))

    q=st.text_area(
        'Or ask EduPulse Academic Copilot',
        placeholder='Example: Compare this student’s earlier-level performance with the latest result and explain what changed.'
    )
    if st.button('Ask Custom Question') and q.strip():
        st.markdown('### Copilot Response')
        st.write(ai_copilot.answer(q,context))

    st.caption(
        "The Copilot is constrained to the academic evidence stored in EduPulse. It should not invent student circumstances or make final disciplinary decisions."
    )

# REPORTS
elif page=='Reports':
    if not is_admin:
        st.error('This area is restricted to authorised academic administrators.')
        st.stop()
    hero(
        'Reports',
        'A management-friendly summary of student monitoring, predictive risk, interventions and follow-up activity.'
    )

    students=visible_students(user)
    watch=db.list_watchlist(user['school_id'])
    ints_all=db.list_interventions(user['school_id'])
    fol_all=db.list_followups(user['school_id'])

    st.markdown('### Current academic-support picture')
    a,b,c,d=st.columns(4)
    a.metric('Students Monitored',len(students))
    b.metric('Open Early Warnings',len(watch))
    c.metric('Interventions Recorded',len(ints_all))
    d.metric('Follow-ups Recorded',len(fol_all))

    if watch:
        risk_df=pd.DataFrame(watch)
        risk_counts=risk_df['risk_level'].value_counts().rename_axis('Risk Level').reset_index(name='Students')
        st.markdown('### Students currently needing attention')
        st.caption('These students currently have an open Moderate, High or Critical predictive-risk case.')
        st.dataframe(risk_counts,hide_index=True,use_container_width=True)

    month=st.selectbox('Reporting month',list(range(1,13)),index=datetime.now().month-1)
    year=st.number_input('Reporting year',2020,2040,datetime.now().year)

    ints=pd.DataFrame(ints_all)
    fol=pd.DataFrame(fol_all)
    if not ints.empty:
        dts=pd.to_datetime(ints['created_at'],errors='coerce')
        ints=ints[(dts.dt.month==month)&(dts.dt.year==year)]
    if not fol.empty:
        dts=pd.to_datetime(fol['created_at'],errors='coerce')
        fol=fol[(dts.dt.month==month)&(dts.dt.year==year)]

    st.markdown('### Activity during the selected month')
    x,y=st.columns(2)
    x.metric('New Intervention Records',len(ints))
    y.metric('Follow-up Reviews',len(fol))

    if not ints.empty:
        st.markdown('#### Intervention activity')
        show=ints.rename(columns={
            'full_name':'Student','programme':'Course','intervention_type':'Intervention',
            'owner_name':'Responsible Officer','status':'Status','review_date':'Review Date'
        })
        cols=[c for c in ['Student','Course','Intervention','Responsible Officer','Status','Review Date'] if c in show.columns]
        st.dataframe(show[cols],hide_index=True,use_container_width=True)
        st.download_button('Download Intervention CSV',show[cols].to_csv(index=False).encode(),f'interventions_{year}_{month:02d}.csv','text/csv')
    else:
        st.info('No intervention was recorded during the selected month.')

    if not fol.empty:
        st.markdown('#### Follow-up outcomes')
        showf=fol.rename(columns={
            'full_name':'Student','programme':'Course','progress_status':'Progress',
            'observed_gpa':'Observed GPA','observed_cgpa':'Observed CGPA',
            'attendance_rate':'Attendance %','observation_date':'Observation Date'
        })
        cols=[c for c in ['Student','Course','Observation Date','Observed GPA','Observed CGPA','Attendance %','Progress'] if c in showf.columns]
        st.dataframe(showf[cols],hide_index=True,use_container_width=True)
        if 'Progress' in showf.columns:
            progress_counts=showf['Progress'].value_counts()
            st.caption(
                f"Follow-up interpretation: {int(progress_counts.get('IMPROVING',0))} improving, "
                f"{int(progress_counts.get('STABLE',0))} stable and {int(progress_counts.get('DECLINING',0))} declining."
            )

    printable=(
        f"<html><body><h1>{html.escape(school['name'])} - EduPulse Academic Support Report</h1>"
        f"<p>Reporting period: {year}-{month:02d}</p>"
        f"<p>Students monitored: {len(students)} | Open early warnings: {len(watch)} | "
        f"Interventions this month: {len(ints)} | Follow-ups this month: {len(fol)}</p>"
        f"<h2>Interventions</h2>{ints.to_html(index=False) if not ints.empty else '<p>No interventions.</p>'}"
        f"<h2>Follow-up</h2>{fol.to_html(index=False) if not fol.empty else '<p>No follow-ups.</p>'}"
        f"</body></html>"
    )
    st.download_button('Download Printable Management Report',printable.encode(),f'edupulse_report_{year}_{month:02d}.html','text/html')

# SETTINGS
elif page=='School Settings':
    if not can_view_settings:
        st.error('School Settings is restricted to authorised academic administrators.')
        st.stop()

    hero('School Settings','Institution configuration and registration access.')

    if is_top_tier:
        st.caption('Director and Rector have full authority to update institution settings and registration verification codes.')

        with st.form('settings'):
            a,b=st.columns(2)
            with a:
                name=st.text_input('Institution name',value=school['name'])
                code=st.text_input('Institution code',value=school['code'])
                session=st.text_input('Current academic session',value=school.get('current_academic_session') or '2025/2026')
            with b:
                scale=st.selectbox(
                    'Official GPA / CGPA scale',[5.0,4.0],
                    index=0 if float(school['gpa_scale'])==5.0 else 1
                )
                duration=st.selectbox(
                    'Default course duration (years)',[4,5],
                    index=0 if int(school['default_programme_duration'])==4 else 1
                )

            save=st.form_submit_button('Save Institution Settings',type='primary')

        if save:
            try:
                db.update_school_settings(
                    user['school_id'],name,code,scale,duration,session
                )
                db.log_action(user,'SCHOOL_SETTINGS','school',user['school_id'])
                st.success('Institution settings updated.')
                rerun()
            except Exception as e:
                st.error(str(e))

        st.markdown('### Registration Verification Codes')
        st.caption('Use these private codes to authorise new staff accounts for each post.')
        code_roles=['PRINCIPAL','VICE_PRINCIPAL','INTERVENTION_COMMITTEE','ACADEMIC_ADVISER','LECTURER']
        role_label=st.selectbox('Post',[auth.role_label(r) for r in code_roles])
        selected_role={auth.role_label(r):r for r in code_roles}[role_label]
        new_code=st.text_input(f'New {role_label} code',type='password')

        if st.button('Update Verification Code',type='primary'):
            try:
                auth.change_role_code(user,selected_role,new_code)
                st.success(f'{role_label} verification code updated.')
            except Exception as e:
                st.error(str(e))
    else:
        st.info('You can view the institution configuration. Only the Director or Rector can change these settings or registration verification codes.')
        a,b=st.columns(2)
        with a:
            st.text_input('Institution name',value=school['name'],disabled=True)
            st.text_input('Institution code',value=school['code'],disabled=True)
            st.text_input('Current academic session',value=school.get('current_academic_session') or '2025/2026',disabled=True)
        with b:
            st.text_input('Official GPA / CGPA scale',value=f"{float(school['gpa_scale']):.1f}",disabled=True)
            st.text_input('Default course duration (years)',value=str(int(school['default_programme_duration'])),disabled=True)

        st.markdown('### Registration Access')
        st.caption('Verification codes are hidden and can only be changed by the Director or Rector.')

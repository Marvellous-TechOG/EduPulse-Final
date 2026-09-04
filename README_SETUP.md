# EduPulse — Final Presentation Build

## Start

```cmd
conda activate edu_app
cd C:\Users\owner\Project\EduPulse_Final_Presentation
python -m pip install -r requirements.txt
python train_dynamic_models.py
streamlit run dashboard.py
```

Place `nigeria_student_performance.xlsx` inside the `data` folder before training.

## Registration

The first run configures the institution and private verification codes for each post. After that, the public authentication screen provides **Login**, **Register**, and **Forgot Password**.

Every registrant selects the post they hold and must supply the verification code for that post. Sensitive pages remain role-restricted.

## When pages activate

- **Student Intelligence:** works as soon as a student has one saved semester GPA. A fresher without GPA shows a baseline profile instead of a fake prediction.
- **Early Warning Center:** a case is opened automatically when a saved or bulk-screened student receives Moderate, High, or Critical predictive risk.
- **Intervention Management:** becomes useful once an open early-warning case exists.
- **Follow-up Monitoring:** becomes useful after an intervention has been recorded.
- **Academic Copilot:** always opens; for students with results it explains prediction/evidence, while freshers receive a baseline academic brief.

## Bulk testing

Use `test_bulk_screening.csv`. It contains multiple students with improving, declining, stable, and final-year academic histories.

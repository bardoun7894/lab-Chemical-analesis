"""
Add machine_id column to pipe_stages table
"""
from app import create_app, db
from sqlalchemy import text

app = create_app()

with app.app_context():
    try:
        # Add machine_id column
        db.session.execute(text('''
            ALTER TABLE pipe_stages
            ADD COLUMN machine_id INTEGER REFERENCES machines(id)
        '''))
        db.session.commit()
        print('[OK] machine_id column added to pipe_stages table')
    except Exception as e:
        if 'duplicate column name' in str(e).lower() or 'already exists' in str(e).lower():
            print('[OK] machine_id column already exists')
        else:
            print(f'Error: {e}')
            db.session.rollback()

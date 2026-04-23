"""Show all users and reset passwords by ID."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bcrypt
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/verdictcouncil")
engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    rows = conn.execute(text("SELECT id, email, role FROM users")).fetchall()
    print("Current users:")
    for r in rows:
        print(f"  id={r[0]}  email={r[1]}  role={r[2]}")

    # Reset by UUID — only update email and password, leave role alone
    resets = [
        ("00000000-0000-4000-a000-000000000001", "judge@verdictcouncil.sg", "password"),
        ("00000000-0000-4000-a000-000000000002", "admin@verdictcouncil.sg", "admin123"),
    ]
    for uid, email, pwd in resets:
        h = bcrypt.hashpw(pwd.encode(), bcrypt.gensalt()).decode()
        r = conn.execute(
            text("UPDATE users SET email=:e, password_hash=:h WHERE id=:id RETURNING email, role"),
            {"e": email, "h": h, "id": uid}
        ).fetchone()
        if r:
            ok = bcrypt.checkpw(pwd.encode(), h.encode())
            print(f"UPDATED {'OK' if ok else 'FAIL'}: {r[0]} ({r[1]}) / '{pwd}'")
        else:
            print(f"NOT FOUND: id={uid}")
    conn.commit()

print("\nLogin with:")
for _, email, pwd in resets:
    print(f"  {email}  /  {pwd}")

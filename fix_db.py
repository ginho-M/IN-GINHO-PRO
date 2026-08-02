# -*- coding: utf-8 -*-
import sqlite3

conn = sqlite3.connect('inginho_final.db')
cursor = conn.cursor()

cursor.execute("PRAGMA table_info(user)")
cols = [col[1] for col in cursor.fetchall()]

if 'auto_scan' not in cols:
    cursor.execute("ALTER TABLE user ADD COLUMN auto_scan BOOLEAN DEFAULT 0")
    print("Colonne auto_scan ajoutee")
else:
    print("Colonne deja presente")

conn.commit()
conn.close()
print("Base mise a jour")
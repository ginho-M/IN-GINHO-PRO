import sqlite3

# Connexion a la base
conn = sqlite3.connect('inginho_final.db')
cursor = conn.cursor()

# Demander l'email et le nouveau mot de passe
email = input("admin@admin.com : ")
new_password = input("*Gm0811813002625# : ")

# Mettre a jour
cursor.execute("UPDATE user SET password = ? WHERE email = ?", (new_password, email))

if cursor.rowcount > 0:
    print(f"✅ Mot de passe mis a jour pour {email}")
    print(f"🔑 Nouveau mot de passe : {new_password}")
else:
    print("❌ Aucun utilisateur trouve avec cet email.")
    print("Voici la liste des emails existants :")
    cursor.execute("SELECT email FROM user")
    for row in cursor.fetchall():
        print(f"   - {row[0]}")

conn.commit()
conn.close()
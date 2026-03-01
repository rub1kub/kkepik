import sqlite3
import config

def migrate_database():
    """Добавляет новые столбцы is_class_teacher и class_group в таблицу users"""
    conn = sqlite3.connect(config.DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Удаляем таблицу users_new, если она существует
        cursor.execute("DROP TABLE IF EXISTS users_new")
        
        # Создаем новую таблицу с дополнительными столбцами
        cursor.execute("""
            CREATE TABLE users_new (
                user_id INTEGER PRIMARY KEY,
                role TEXT NOT NULL,
                name_or_group TEXT NOT NULL,
                is_class_teacher INTEGER DEFAULT 0,
                class_group TEXT
            )
        """)
        
        # Копируем данные из старой таблицы в новую
        cursor.execute("""
            INSERT INTO users_new (user_id, role, name_or_group)
            SELECT user_id, role, name_or_group
            FROM users
        """)
        
        # Удаляем старую таблицу
        cursor.execute("DROP TABLE users")
        
        # Переименовываем новую таблицу
        cursor.execute("ALTER TABLE users_new RENAME TO users")
        
        conn.commit()
        print("✅ База данных успешно обновлена!")
        
    except Exception as e:
        print(f"❌ Ошибка при обновлении базы данных: {e}")
        conn.rollback()
    
    finally:
        conn.close()

if __name__ == "__main__":
    migrate_database() 
from django.db import migrations


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('event', '0012_rename_event_label_idx_event_event_label_79334f_idx_and_more'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS event_embedding_hnsw_idx
                ON event_event
                USING hnsw (embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 200);
            """,
            reverse_sql="""
                DROP INDEX IF EXISTS event_embedding_hnsw_idx;
            """,
        ),
    ]

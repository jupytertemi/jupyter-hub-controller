from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('face_training', '0005_facetraining_quality_score_augmentation_type'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS face_training_embedding_hnsw_idx
                ON face_training_facetraining
                USING hnsw (embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 200);
            """,
            reverse_sql="""
                DROP INDEX IF EXISTS face_training_embedding_hnsw_idx;
            """,
        ),
    ]

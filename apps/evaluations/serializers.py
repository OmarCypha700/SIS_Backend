from rest_framework import serializers

from .models import CommentStatus, Question, Questionnaire, QuestionKind


class QuestionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Question
        fields = ["id", "order", "text", "label", "kind", "required"]
        read_only_fields = ["id"]


class QuestionnaireSerializer(serializers.ModelSerializer):
    questions = QuestionSerializer(many=True)
    is_used = serializers.BooleanField(read_only=True)
    question_count = serializers.SerializerMethodField()

    class Meta:
        model = Questionnaire
        fields = ["id", "name", "is_active", "is_used", "question_count", "questions", "created_at", "updated_at"]
        read_only_fields = ["is_active", "created_at", "updated_at"]

    def get_question_count(self, questionnaire) -> int:
        return len(questionnaire.questions.all())

    def validate_questions(self, questions):
        if not questions:
            raise serializers.ValidationError("Add at least one question.")
        if not any(q.get("kind", QuestionKind.RATING) == QuestionKind.RATING for q in questions):
            raise serializers.ValidationError("Add at least one rating question.")
        if len(questions) > 40:
            raise serializers.ValidationError("Keep the questionnaire to 40 questions or fewer.")
        return questions

    def _write_questions(self, questionnaire, questions):
        questionnaire.questions.all().delete()
        Question.objects.bulk_create([
            Question(questionnaire=questionnaire, order=q.get("order") or i, text=q["text"], label=q.get("label", ""),
                     kind=q.get("kind", QuestionKind.RATING), required=q.get("required", True))
            for i, q in enumerate(questions, 1)
        ])

    def create(self, validated_data):
        questions = validated_data.pop("questions")
        questionnaire = Questionnaire.objects.create(**validated_data)
        self._write_questions(questionnaire, questions)
        return questionnaire

    def update(self, instance, validated_data):
        questions = validated_data.pop("questions", None)
        instance.name = validated_data.get("name", instance.name)
        instance.save(update_fields=["name", "updated_at"])
        if questions is not None:
            self._write_questions(instance, questions)
        return instance


class SubmitSerializer(serializers.Serializer):
    lecturer = serializers.IntegerField()
    answers = serializers.DictField(child=serializers.CharField(allow_blank=True, max_length=2000))


class CommentReviewSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=[CommentStatus.APPROVED, CommentStatus.HIDDEN])
    reason = serializers.CharField(required=False, allow_blank=True, max_length=500)

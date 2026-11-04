from decimal import Decimal

from rest_framework import serializers

from apps.academics.models import AcademicYear, Semester
from apps.institutions.models import FeeBillingPeriod, InstitutionProfile

from . import fees
from .models import EntryKind, FeeSchedule, Payment


class FeeItemInputSerializer(serializers.Serializer):
    id = serializers.IntegerField(required=False, allow_null=True)
    description = serializers.CharField(max_length=120)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))


class FeeScheduleSerializer(serializers.ModelSerializer):
    items = FeeItemInputSerializer(many=True)
    programme_code = serializers.CharField(source="programme.code", read_only=True)
    programme_name = serializers.CharField(source="programme.name", read_only=True)
    level_name = serializers.CharField(source="level.name", read_only=True)
    academic_year_name = serializers.CharField(source="academic_year.name", read_only=True)
    semester_name = serializers.CharField(source="semester.name", read_only=True, default=None)
    period_label = serializers.CharField(read_only=True)
    total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    billed_students = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = FeeSchedule
        fields = ["id", "programme", "programme_code", "programme_name", "level", "level_name", "academic_year",
                  "academic_year_name", "semester", "semester_name", "period_label", "notes", "items", "total",
                  "billed_students", "created_at", "updated_at"]
        read_only_fields = ["created_at", "updated_at"]
        validators = []  # uniqueness is reported in validate() with a clearer message

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["items"] = [{"id": i.id, "description": i.description, "amount": str(i.amount)} for i in instance.items.all()]
        return data

    def validate(self, attrs):
        get = lambda f: attrs.get(f, getattr(self.instance, f, None))  # noqa: E731
        year, semester = get("academic_year"), get("semester")
        per_year = InstitutionProfile.load().fee_billing_period == FeeBillingPeriod.ACADEMIC_YEAR
        if per_year and semester is not None:
            raise serializers.ValidationError({"semester": "Fees are billed per academic year: leave the semester empty "
                                                           "(or change the billing period in Institution settings)."})
        if not per_year and semester is None:
            raise serializers.ValidationError({"semester": "Fees are billed per semester: choose the semester."})
        if semester is not None and semester.academic_year_id != year.pk:
            raise serializers.ValidationError({"semester": "This semester belongs to a different academic year."})
        clash = FeeSchedule.objects.filter(programme=get("programme"), level=get("level"), academic_year=year,
                                           semester=semester)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("Fees for this programme, level and period already exist. Edit those instead.")
        if "items" in attrs and not attrs["items"]:
            raise serializers.ValidationError({"items": "Add at least one fee item."})
        return attrs

    def create(self, validated_data):
        items = validated_data.pop("items")
        schedule = FeeSchedule.objects.create(**validated_data)
        return fees.save_items(schedule, items, actor=self.context["request"].user)

    def update(self, instance, validated_data):
        items = validated_data.pop("items", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        if items is not None:
            fees.save_items(instance, items, actor=self.context["request"].user)
        return instance


class BillSerializer(serializers.Serializer):
    dry_run = serializers.BooleanField(default=True)
    students = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False, max_length=5000)


class CopySchedulesSerializer(serializers.Serializer):
    source_year = serializers.PrimaryKeyRelatedField(queryset=AcademicYear.objects.all())
    target_year = serializers.PrimaryKeyRelatedField(queryset=AcademicYear.objects.all())

    def validate(self, attrs):
        if attrs["source_year"] == attrs["target_year"]:
            raise serializers.ValidationError("Choose two different academic years.")
        return attrs


class ManualPaymentSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    channel = serializers.ChoiceField(choices=["bank", "cash", "mobile_money", "bank_transfer", "cheque"], default="bank")
    reference = serializers.CharField(max_length=64,
                                      help_text="Bank slip, teller or cashier receipt number. Must be unique.")
    paid_on = serializers.DateField(required=False, allow_null=True)
    note = serializers.CharField(max_length=255, required=False, allow_blank=True)


class EntrySerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=[EntryKind.WAIVER, EntryKind.REFUND, EntryKind.ADJUSTMENT])
    amount = serializers.DecimalField(max_digits=12, decimal_places=2,
                                      help_text="Waiver and refund: positive. Adjustment: negative lowers the bill.")
    reason = serializers.CharField(max_length=200)
    semester = serializers.PrimaryKeyRelatedField(queryset=Semester.objects.all(), required=False, allow_null=True)


class ReverseSerializer(serializers.Serializer):
    entry = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(max_length=200)


class PaymentSerializer(serializers.ModelSerializer):
    student_number = serializers.CharField(source="student.student_number", read_only=True)
    student_name = serializers.CharField(source="student.full_name", read_only=True)
    programme_code = serializers.CharField(source="student.programme.code", read_only=True)
    source_display = serializers.CharField(source="get_source_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    recorded_by_name = serializers.CharField(source="recorded_by.get_full_name", read_only=True, default="")

    class Meta:
        model = Payment
        fields = ["id", "student", "student_number", "student_name", "programme_code", "reference", "source",
                  "source_display", "status", "status_display", "amount", "fee", "total", "channel", "paid_at",
                  "receipt_number", "gateway_message", "note", "recorded_by_name", "created_at", "last_checked_at",
                  "reversed_at", "reversal_reason"]
        read_only_fields = fields


class MyPaymentSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Payment
        fields = ["reference", "source", "status", "status_display", "amount", "fee", "total", "channel", "paid_at",
                  "receipt_number", "created_at", "reversed_at"]
        read_only_fields = fields


class PayRequestSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("1"))


class ImportRowSerializer(serializers.Serializer):
    row = serializers.IntegerField(required=False, min_value=1)
    student_number = serializers.CharField(max_length=40, allow_blank=True)
    amount = serializers.CharField(max_length=30, allow_blank=True)
    reference = serializers.CharField(max_length=64, allow_blank=True)
    date = serializers.CharField(max_length=10, required=False, allow_blank=True)
    method = serializers.CharField(max_length=30, required=False, allow_blank=True)
    note = serializers.CharField(max_length=255, required=False, allow_blank=True)


class ImportConfirmSerializer(serializers.Serializer):
    rows = ImportRowSerializer(many=True, allow_empty=False, max_length=2000)

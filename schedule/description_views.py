"""Правка описания предмета со страниц дневника (для преподавателя пары)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .descriptions import can_edit_description, clean_description
from .models import Pair


def _back(request):
    target = request.POST.get("next", "")

    if target and url_has_allowed_host_and_scheme(
        target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return target

    return reverse("diary_home")


@login_required
@require_POST
def pair_description(request, pair_id):
    """Сохраняет описание пары: можно администратору и её преподавателю."""
    pair = get_object_or_404(Pair, pk=pair_id)

    if not can_edit_description(request.user, pair.teacher_id):
        raise PermissionDenied

    try:
        text = clean_description(request.POST.get("description"))
    except ValidationError as error:
        messages.error(request, error.messages[0])
        return redirect(_back(request))

    if text != pair.description:
        pair.description = text
        pair.save(update_fields=["description"])

    messages.success(
        request,
        f"Описание предмета «{pair.pair_name}» "
        + ("сохранено." if text else "удалено."),
    )

    return redirect(_back(request))

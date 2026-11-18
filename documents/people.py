def full_name(user):
    patronymic = getattr(getattr(user, "profile", None), "patronymic", "")
    return " ".join(part.strip() for part in (user.last_name, user.first_name, patronymic) if part.strip())

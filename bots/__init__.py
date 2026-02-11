from . import personal, formalization, training, product, email

BOTS = {
    "personal": {
        "label": "Personal Problems",
        "runner": personal.run,
    },
    "formalization": {
        "label": "Problem Formalization",
        "runner": formalization.run,
    },
    "training": {
        "label": "Generic Training",
        "runner": training.run,
    },
    "product": {
        "label": "Product Understanding",
        "runner": product.run,
    },
    "email": {
        "label": "Email Writer",
        "runner": email.run,
    },
}
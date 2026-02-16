from . import personal, formalization, training, product, email

BOTS = {
    "personal": {
        "label": "Personal Problems",
        "runner": personal.run,
        "runner_stream": personal.run_stream,
    },
    "formalization": {
        "label": "Problem Formalization",
        "runner": formalization.run,
        "runner_stream": formalization.run_stream,
    },
    "training": {
        "label": "Generic Training",
        "runner": training.run,
        "runner_stream": training.run_stream,
    },
    "product": {
        "label": "Product Understanding",
        "runner": product.run,
        "runner_stream": product.run_stream,
    },
    "email": {
        "label": "Email Writer",
        "runner": email.run,
        "runner_stream": email.run_stream,
    },
}

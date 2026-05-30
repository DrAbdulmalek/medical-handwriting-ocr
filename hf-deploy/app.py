import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from app.gradio_app import build_gradio_app

demo = build_gradio_app()
demo.launch(
    server_name="0.0.0.0",
    server_port=7860,
    show_error=True,
)

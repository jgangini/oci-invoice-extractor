import json
import logging

from fdk import response

from extractor.service import invoke


def handler(ctx, data=None):
    try:
        event = json.loads(data.getvalue())
        if not isinstance(event, dict):
            raise ValueError("event")
    except (AttributeError, ValueError):
        return response.Response(ctx, status_code=400, response_data="Invalid event")
    try:
        result = invoke(event, ctx.FnID())
        logging.info("call=%s batch=%s status=%s", ctx.CallID(),
                     result.get("batch_id"), result["status"])
    except Exception as exc:
        logging.error("call=%s problem=%s", ctx.CallID(), type(exc).__name__)
        raise RuntimeError("Invoice processing failed") from None
    return response.Response(ctx, response_data=json.dumps(result), headers={"Content-Type": "application/json"})

import logging

from typing import Any
from uuid import uuid4

import httpx

from a2a.client import A2ACardResolver, A2AClient
from a2a.types import (
    AgentCard,
    MessageSendParams,
    SendMessageRequest,
    SendStreamingMessageRequest,
)
from a2a.utils.constants import (
    AGENT_CARD_WELL_KNOWN_PATH,
    EXTENDED_AGENT_CARD_PATH,
)


def _extract_texts(response_dict: dict) -> list[str]:
    """Pull out only the 'text' values from an A2A response dict's parts,
    regardless of which shape they show up in:
      - result.artifacts[].parts[]   (non-streaming, completed task)
      - result.artifact.parts[]      (streaming artifact-update)
      - result.status.message.parts[] (streaming status-update)
    """
    # texts: list[str] = []
    # result = response_dict.get('result') if isinstance(response_dict, dict) else None
    # if not isinstance(result, dict):
    #     return texts
    #
    # def collect_parts(parts):
    #     for part in parts or []:
    #         if isinstance(part, dict) and part.get('kind') == 'text' and part.get('text'):
    #             texts.append(part['text'])
    #
    # for artifact in result.get('artifacts', []) or []:
    #     collect_parts(artifact.get('parts'))
    #
    # artifact = result.get('artifact')
    # if isinstance(artifact, dict):
    #     collect_parts(artifact.get('parts'))
    #
    # status = result.get('status')
    # if isinstance(status, dict):
    #     message = status.get('message')
    #     if isinstance(message, dict):
    #         collect_parts(message.get('parts'))
    #
    # return texts
    events: list[dict] = []
    if not isinstance(response_dict, dict):
        return events

    error = response_dict.get('error')
    if isinstance(error, dict):
        events.append({
            'kind': 'error',
            'state': None,
            'final': None,
            'text': f"{error.get('code', '?')}: {error.get('message', 'unknown error')}",
        })
        return events

    result = response_dict.get('result')
    if not isinstance(result, dict):
        return events

    top_kind = result.get('kind')  # 'task' | 'status-update' | 'artifact-update'
    top_state = (result.get('status') or {}).get('state')

    def collect_parts(parts, kind, state, final):
        for part in parts or []:
            if isinstance(part, dict) and part.get('kind') == 'text' and part.get('text'):
                events.append({
                    'kind': kind,
                    'state': state,
                    'final': final,
                    'text': part['text'],
                })

    # Non-streaming: completed task with one or more artifacts
    for artifact in result.get('artifacts', []) or []:
        collect_parts(artifact.get('parts'), top_kind or 'task', top_state, None)

    # Streaming: artifact-update event
    artifact = result.get('artifact')
    if isinstance(artifact, dict):
        collect_parts(
            artifact.get('parts'),
            top_kind or 'artifact-update',
            top_state,
            result.get('final'),
        )

    # Streaming: status-update event carrying an interim agent message
    status = result.get('status')
    if isinstance(status, dict):
        message = status.get('message')
        if isinstance(message, dict):
            collect_parts(
                message.get('parts'),
                top_kind or 'status-update',
                status.get('state'),
                result.get('final'),
            )

    return events


def _print_texts(response_dict: dict) -> None:
    for text in _extract_texts(response_dict):
        print(text)


async def main() -> None:
    # Configure logging to show INFO level messages
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)  # Get a logger instance

    # --8<-- [start:A2ACardResolver]

    base_url = 'http://localhost:10000'

    async with httpx.AsyncClient(timeout=300.0) as httpx_client:
        # Initialize A2ACardResolver
        resolver = A2ACardResolver(
            httpx_client=httpx_client,
            base_url=base_url,
            # agent_card_path uses default, extended_agent_card_path also uses default
        )
        # --8<-- [end:A2ACardResolver]

        # Fetch Public Agent Card and Initialize Client
        final_agent_card_to_use: AgentCard | None = None

        try:
            logger.info(
                f'Attempting to fetch public agent card from: {base_url}{AGENT_CARD_WELL_KNOWN_PATH}'
            )
            _public_card = (
                await resolver.get_agent_card()
            )  # Fetches from default public path
            logger.info('Successfully fetched public agent card:')
            logger.info(
                _public_card.model_dump_json(indent=2, exclude_none=True)
            )
            final_agent_card_to_use = _public_card
            logger.info(
                '\nUsing PUBLIC agent card for client initialization (default).'
            )

            if _public_card.supports_authenticated_extended_card:
                try:
                    logger.info(
                        '\nPublic card supports authenticated extended card. '
                        'Attempting to fetch from: '
                        f'{base_url}{EXTENDED_AGENT_CARD_PATH}'
                    )
                    auth_headers_dict = {
                        'Authorization': 'Bearer dummy-token-for-extended-card'
                    }
                    _extended_card = await resolver.get_agent_card(
                        relative_card_path=EXTENDED_AGENT_CARD_PATH,
                        http_kwargs={'headers': auth_headers_dict},
                    )
                    logger.info(
                        'Successfully fetched authenticated extended agent card:'
                    )
                    logger.info(
                        _extended_card.model_dump_json(
                            indent=2, exclude_none=True
                        )
                    )
                    final_agent_card_to_use = (
                        _extended_card  # Update to use the extended card
                    )
                    logger.info(
                        '\nUsing AUTHENTICATED EXTENDED agent card for client '
                        'initialization.'
                    )
                except Exception as e_extended:
                    logger.warning(
                        f'Failed to fetch extended agent card: {e_extended}. '
                        'Will proceed with public card.',
                        exc_info=True,
                    )
            elif (
                _public_card
            ):  # supports_authenticated_extended_card is False or None
                logger.info(
                    '\nPublic card does not indicate support for an extended card. Using public card.'
                )

        except Exception as e:
            logger.error(
                f'Critical error fetching public agent card: {e}', exc_info=True
            )
            raise RuntimeError(
                'Failed to fetch the public agent card. Cannot continue.'
            ) from e

        # --8<-- [start:send_message]
        client = A2AClient(
            httpx_client=httpx_client, agent_card=final_agent_card_to_use
        )
        logger.info('A2AClient initialized.')

        send_message_payload: dict[str, Any] = {
            'message': {
                'role': 'user',
                'parts': [
                    {'kind': 'text', 'text': 'Connect to the server.'}
                ],
                'message_id': uuid4().hex,
            },
        }
        logger.info('Send message.')
        logger.info(send_message_payload)
        request = SendMessageRequest(
            id=str(uuid4()), params=MessageSendParams(**send_message_payload)
        )

        response = await client.send_message(request)
        _print_texts(response.model_dump(mode='json', exclude_none=True))
        # --8<-- [end:send_message]

        # --8<-- [start:Multiturn]
        # send_message_payload_multiturn: dict[str, Any] = {
        #     'message': {
        #         'role': 'user',
        #         'parts': [
        #             {
        #                 'kind': 'text',
        #                 'text': 'How much is the exchange rate for 1 USD?',
        #             }
        #         ],
        #         'message_id': uuid4().hex,
        #     },
        # }
        # request = SendMessageRequest(
        #     id=str(uuid4()),
        #     params=MessageSendParams(**send_message_payload_multiturn),
        # )

        # response = await client.send_message(request)
        # print(response.model_dump(mode='json', exclude_none=True))
        #
        # task_id = response.root.result.id
        # context_id = response.root.result.context_id
        #
        # second_send_message_payload_multiturn: dict[str, Any] = {
        #     'message': {
        #         'role': 'user',
        #         'parts': [{'kind': 'text', 'text': 'CAD'}],
        #         'message_id': uuid4().hex,
        #         'task_id': task_id,
        #         'context_id': context_id,
        #     },
        # }
        #
        # second_request = SendMessageRequest(
        #     id=str(uuid4()),
        #     params=MessageSendParams(**second_send_message_payload_multiturn),
        # )
        #
        # second_response = await client.send_message(second_request)
        # print(second_response.model_dump(mode='json', exclude_none=True))
        # --8<-- [end:Multiturn]

        # --8<-- [start:send_message_streaming]

        streaming_request = SendStreamingMessageRequest(
            id=str(uuid4()), params=MessageSendParams(**send_message_payload)
        )

        stream_response = client.send_message_streaming(streaming_request)

        async for chunk in stream_response:
            _print_texts(chunk.model_dump(mode='json', exclude_none=True))
        # --8<-- [end:send_message_streaming]


if __name__ == '__main__':
    import asyncio

    asyncio.run(main())

from typing import List, Optional, Tuple, Union

import torch
from transformers import PreTrainedModel
from transformers.modeling_outputs import CausalLMOutputWithPast
from transformers.models.auto import AutoModelForCausalLM

from olmo.config import ModelConfig
from olmo.model import Olmo

from .configuration_olmo import OLMoConfig


def create_model_config_from_pretrained_config(config: OLMoConfig):
    """
    Utility function
    """
    model_config = ModelConfig(
        d_model=config.d_model,
        n_heads=config.n_heads,
        n_layers=config.n_layers,
        mlp_ratio=config.mlp_ratio,
        activation_type=config.activation_type,
        block_type=config.block_type,
        alibi=config.alibi,
        alibi_bias_max=config.alibi_bias_max,
        rope=config.rope,
        flash_attention=config.flash_attention,
        attention_dropout=config.attention_dropout,
        attention_layer_norm=config.attention_layer_norm,
        multi_query_attention=config.multi_query_attention,
        residual_dropout=config.residual_dropout,
        embedding_dropout=config.embedding_dropout,
        layer_norm_type=config.layer_norm_type,
        max_sequence_length=config.max_sequence_length,
        include_bias=config.include_bias,
        vocab_size=config.vocab_size,
        embedding_size=config.embedding_size,
        eos_token_id=config.eos_token_id,
        pad_token_id=config.pad_token_id,
        init_device=config.init_device,
        init_std=config.init_std,
        precision=config.precision,
    )
    return model_config


class OLMoForCausalLM(PreTrainedModel):
    """
    Extremely barebones HF model wrapper.
    """

    config_class = OLMoConfig
    base_model_prefix = "model"

    def __init__(self, config: OLMoConfig, model: Optional[Olmo] = None):
        super().__init__(config)

        if not model:
            model_config = create_model_config_from_pretrained_config(config)
            self.model = Olmo(model_config, init_params=True)
        else:
            self.model = model

    def forward(
        self,
        input_ids: torch.LongTensor = None,
        attention_mask: Optional[torch.Tensor] = None,
        past_key_values: Optional[List[torch.FloatTensor]] = None,
        labels: Optional[torch.LongTensor] = None,
        use_cache: Optional[bool] = None,
        output_attentions: Optional[bool] = None,
        output_hidden_states: Optional[bool] = None,
        return_dict: Optional[bool] = None,
    ) -> Union[Tuple, CausalLMOutputWithPast]:
        if use_cache is None:
            use_cache = self.config.use_cache

        return_dict = return_dict if return_dict is not None else self.config.use_return_dict

        # decoder outputs consists of (dec_features, layer_state, dec_hidden, dec_attn)
        outputs = self.model.forward(
            input_ids=input_ids,
            attention_mask=attention_mask,
            past_key_values=past_key_values,
            use_cache=use_cache,
        )

        logits = outputs.logits

        loss = None
        if labels is not None:
            # Shift so that tokens < n predict n
            shift_logits = logits[..., :-1, :].contiguous()
            shift_labels = labels[..., 1:].contiguous()
            # Flatten the tokens
            loss_fct = torch.nn.CrossEntropyLoss()
            shift_logits = shift_logits.view(-1, self.config.embedding_size)
            shift_labels = shift_labels.view(-1)
            # Enable model parallelism
            shift_labels = shift_labels.to(shift_logits.device)
            loss = loss_fct(shift_logits, shift_labels)

        if not return_dict:
            output = (logits,) + outputs[1:]
            return (loss,) + output if loss is not None else output

        return CausalLMOutputWithPast(
            loss=loss,
            logits=logits,
            past_key_values=outputs.attn_key_values,
        )

    def can_generate(self) -> bool:
        return True

    def prepare_inputs_for_generation(
        self, input_ids: torch.LongTensor, past_key_values: Optional[List[Tuple]] = None, **kwargs
    ):
        if past_key_values:
            # This is because we want the model to only process the last generated token.
            input_ids = input_ids[:, -1:]
        model_inputs = {"input_ids": input_ids, "past_key_values": past_key_values}

        model_inputs.update(kwargs)
        model_inputs["use_cache"] = kwargs.pop("use_cache", self.config.use_cache)
        return model_inputs

    # TODO: these are required to make the implementation complete.
    # def resize_position_embeddings(self, new_num_position_embeddings: int):
    #     pass
    #
    # def get_position_embeddings(self) -> Union[nn.Embedding, Tuple[nn.Embedding]]:
    #     pass
    #
    # def _reorder_cache(self, past_key_values, beam_idx):
    #     pass

    def get_input_embeddings(self) -> torch.nn.Module:
        return self.model.transformer.wte

    def set_input_embeddings(self, value: torch.nn.Module):
        self.model.transformer.wte = value

    def get_output_embeddings(self):
        if self.config.weight_tying:
            return self.model.transformer.wte
        else:
            return self.model.transformer.ff_out

    def set_output_embeddings(self, value: torch.nn.Module):
        if self.config.weight_tying:
            self.model.transformer.wte = value
        else:
            self.model.transformer.ff_out = value

    def tie_weights(self):
        if self.config.weight_tying:
            self.model.transformer.ff_out = self.model.transformer.wte


# Register the model so that it is available for transformer pipelines, auto-loading, etc.
AutoModelForCausalLM.register(OLMoConfig, OLMoForCausalLM)

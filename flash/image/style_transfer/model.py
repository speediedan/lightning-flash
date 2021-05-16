from typing import Any, cast, Dict, Mapping, NoReturn, Optional, Sequence, Type, Union

import pystiche.demo
import torch
from pystiche import enc, loss, ops
from pystiche.image import read_image
from torch import nn
from torch.optim.lr_scheduler import _LRScheduler

from flash.core.data.data_source import DefaultDataKeys
from flash.core.data.process import Serializer
from flash.core.model import Task
from flash.core.registry import FlashRegistry
from flash.image.style_transfer import STYLE_TRANSFER_BACKBONES

from ._utils import raise_not_supported

__all__ = ["StyleTransfer"]


class StyleTransfer(Task):
    backbones: FlashRegistry = STYLE_TRANSFER_BACKBONES

    def __init__(
        self,
        style_image: Optional[Union[str, torch.Tensor]] = None,
        model: Optional[nn.Module] = None,
        backbone: str = "vgg16",
        content_layer: str = "relu2_2",
        content_weight: float = 1e5,
        style_layers: Sequence[str] = ("relu1_2", "relu2_2", "relu3_3", "relu4_3"),
        style_weight: float = 1e10,
        optimizer: Union[Type[torch.optim.Optimizer], torch.optim.Optimizer] = torch.optim.Adam,
        optimizer_kwargs: Optional[Dict[str, Any]] = None,
        scheduler: Optional[Union[Type[_LRScheduler], str, _LRScheduler]] = None,
        scheduler_kwargs: Optional[Dict[str, Any]] = None,
        learning_rate: float = 1e-3,
        serializer: Optional[Union[Serializer, Mapping[str, Serializer]]] = None,
    ):
        self.save_hyperparameters(ignore="style_image")

        if style_image is None:
            style_image = self.default_style_image()
        elif isinstance(style_image, str):
            style_image = read_image(style_image)

        if model is None:
            model = pystiche.demo.transformer()

        perceptual_loss = self._get_perceptual_loss(
            backbone=backbone,
            content_layer=content_layer,
            content_weight=content_weight,
            style_layers=style_layers,
            style_weight=style_weight,
        )
        perceptual_loss.set_style_image(style_image)

        super().__init__(
            model=model,
            loss_fn=perceptual_loss,
            optimizer=optimizer,
            optimizer_kwargs=optimizer_kwargs,
            scheduler=scheduler,
            scheduler_kwargs=scheduler_kwargs,
            learning_rate=learning_rate,
            serializer=serializer,
        )

        self.perceptual_loss = perceptual_loss

    def default_style_image(self) -> torch.Tensor:
        return pystiche.demo.images()["paint"].read(size=256)

    @staticmethod
    def _modified_gram_loss(encoder: enc.Encoder, *, score_weight: float) -> ops.EncodingComparisonOperator:
        # The official PyTorch examples as well as the reference implementation of the original author contain an
        # oversight: they normalize the representation twice by the number of channels. To be compatible with them, we
        # do the same here.
        class GramOperator(ops.GramOperator):

            def enc_to_repr(self, enc: torch.Tensor) -> torch.Tensor:
                repr = super().enc_to_repr(enc)
                num_channels = repr.size()[1]
                return repr / num_channels

        return GramOperator(encoder, score_weight=score_weight)

    def _get_perceptual_loss(
        self,
        *,
        backbone: str,
        content_layer: str,
        content_weight: float,
        style_layers: Sequence[str],
        style_weight: float,
    ) -> loss.PerceptualLoss:
        mle, _ = cast(enc.MultiLayerEncoder, self.backbones.get(backbone)())
        content_loss = ops.FeatureReconstructionOperator(
            mle.extract_encoder(content_layer), score_weight=content_weight
        )
        style_loss = ops.MultiLayerEncodingOperator(
            mle,
            style_layers,
            lambda encoder, layer_weight: self._modified_gram_loss(encoder, score_weight=layer_weight),
            layer_weights="sum",
            score_weight=style_weight,
        )
        return loss.PerceptualLoss(content_loss, style_loss)

    def training_step(self, batch: Any, batch_idx: int) -> Any:
        input_image = batch[DefaultDataKeys.INPUT]
        self.perceptual_loss.set_content_image(input_image)

        output_image = self(input_image)
        return self.perceptual_loss(output_image).total()

    def validation_step(self, batch: Any, batch_idx: int) -> NoReturn:
        raise_not_supported("validation")

    def test_step(self, batch: Any, batch_idx: int) -> NoReturn:
        raise_not_supported("test")
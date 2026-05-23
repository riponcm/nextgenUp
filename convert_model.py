#!/usr/bin/env python3
"""Convert Real-ESRGAN compact model weights to ONNX format for browser WebGPU inference."""

import os
import sys
import urllib.request

import torch
import torch.nn as nn
import torch.nn.functional as F


class SRVGGNetCompact(nn.Module):
    """Compact VGG-style super-resolution network from Real-ESRGAN."""

    def __init__(self, num_in_ch=3, num_out_ch=3, num_feat=64, num_conv=32, upscale=4, act_type='prelu'):
        super().__init__()
        self.upscale = upscale
        self.body = nn.ModuleList()

        self.body.append(nn.Conv2d(num_in_ch, num_feat, 3, 1, 1))
        self.body.append(self._make_activation(act_type, num_feat))

        for _ in range(num_conv):
            self.body.append(nn.Conv2d(num_feat, num_feat, 3, 1, 1))
            self.body.append(self._make_activation(act_type, num_feat))

        self.body.append(nn.Conv2d(num_feat, num_out_ch * upscale * upscale, 3, 1, 1))
        self.upsampler = nn.PixelShuffle(upscale)

    def _make_activation(self, act_type, num_feat):
        if act_type == 'relu':
            return nn.ReLU(inplace=True)
        elif act_type == 'prelu':
            return nn.PReLU(num_parameters=num_feat)
        elif act_type == 'leakyrelu':
            return nn.LeakyReLU(negative_slope=0.1, inplace=True)
        raise ValueError(f'Unknown activation: {act_type}')

    def forward(self, x):
        out = x
        for layer in self.body:
            out = layer(out)
        out = self.upsampler(out)
        base = F.interpolate(x, scale_factor=self.upscale, mode='nearest')
        out += base
        return out


def main():
    weights_url = 'https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth'
    weights_path = 'realesr-general-x4v3.pth'
    onnx_path = 'static/models/realesr-general-x4v3.onnx'

    if not os.path.exists(weights_path):
        print(f'Downloading weights from {weights_url}...')
        urllib.request.urlretrieve(weights_url, weights_path)
        print('Download complete.')

    print('Loading model...')
    model = SRVGGNetCompact(num_in_ch=3, num_out_ch=3, num_feat=64, num_conv=32, upscale=4, act_type='prelu')

    state_dict = torch.load(weights_path, map_location='cpu')
    if 'params_ema' in state_dict:
        state_dict = state_dict['params_ema']
    elif 'params' in state_dict:
        state_dict = state_dict['params']

    model.load_state_dict(state_dict)
    model.eval()

    print('Exporting to ONNX...')
    dummy_input = torch.randn(1, 3, 64, 64)

    os.makedirs(os.path.dirname(onnx_path), exist_ok=True)
    torch.onnx.export(
        model,
        dummy_input,
        onnx_path,
        opset_version=17,
        input_names=['input'],
        output_names=['output'],
        dynamic_axes={
            'input': {0: 'batch', 2: 'height', 3: 'width'},
            'output': {0: 'batch', 2: 'height', 3: 'width'}
        }
    )

    size_mb = os.path.getsize(onnx_path) / (1024 * 1024)
    print(f'Saved ONNX model to {onnx_path} ({size_mb:.1f} MB)')


if __name__ == '__main__':
    main()

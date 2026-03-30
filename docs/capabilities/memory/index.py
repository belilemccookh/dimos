# Copyright 2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import math
import pickle

import matplotlib
import matplotlib.pyplot as plt

from dimos.mapping.occupancy.inflation import simple_inflate
from dimos.mapping.pointclouds.occupancy import (
    general_occupancy,
)
from dimos.memory2.store.sqlite import SqliteStore
from dimos.memory2.vis.drawing import Drawing
from dimos.memory2.vis.type import Point
from dimos.models.embedding.clip import CLIPModel
from dimos.utils.data import get_data


def plot_mosaic(frames, path, cols=5):
    matplotlib.use("Agg")
    rows = math.ceil(len(frames) / cols)
    aspect = frames[0].width / frames[0].height
    fig_w, fig_h = 12, 12 * rows / (cols * aspect)

    fig, axes = plt.subplots(rows, cols, figsize=(fig_w, fig_h))
    fig.patch.set_facecolor("black")
    for i, ax in enumerate(axes.flat):
        if i < len(frames):
            ax.imshow(frames[i].data)
            for spine in ax.spines.values():
                spine.set_color("black")
                spine.set_linewidth(0)
            ax.set_xticks([])
            ax.set_yticks([])
        else:
            ax.axis("off")
    plt.subplots_adjust(wspace=0.02, hspace=0.02, left=0, right=1, top=1, bottom=0)
    plt.savefig(path, facecolor="black", dpi=100, bbox_inches="tight", pad_inches=0)
    plt.close()


store = SqliteStore(path=get_data("go2_bigoffice.db"))

global_map = pickle.loads(get_data("unitree_go2_bigoffice_map.pickle").read_bytes())

costmap = simple_inflate(general_occupancy(global_map), 0.05)
drawing = Drawing()
drawing.add(costmap)

store.streams.lidar.map(
    lambda obs: drawing.add(Point(obs.pose_stamped, color="red", radius=0.01))
).drain()


# store.streams.color_image.map(
#     lambda obs: drawing.add(Point(obs.pose_stamped, color="#ff0000", radius=0.025))
# ).last()

# store.streams.color_image_embedded.map(lambda obs: drawing.add(obs.pose_stamped)).last()

clip = CLIPModel()
embedded = store.streams.color_image_embedded

imagelist = []

embeddings = embedded.search(clip.embed_text("bottles"), k=20)

drawing.add(embeddings.tap(lambda obs: imagelist.append(obs.data)))

drawing.to_svg("assets/imageposes.svg")
plot_mosaic(imagelist, "assets/images.png", cols=4)

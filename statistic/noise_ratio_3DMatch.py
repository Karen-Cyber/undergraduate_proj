import os
import sys
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)) , ".."))

import numpy as np
import torch
import MinkowskiEngine as ME

from tqdm import tqdm
from easydict import EasyDict as edict

from datasets import datasets
import config
import utils
from utils import ransac
import models

def add_salt(total: int, selected: np.ndarray, noise_ratio: float):
    if noise_ratio < 1e-5:
        return selected
    fullset = set(list(range(total)))
    subbset = set(list(selected))
    rndptsidx = np.random.choice(list(fullset - subbset), size=int(total * noise_ratio), replace=False)
    return np.concatenate([selected, rndptsidx])


if __name__ == "__main__":
    args = config.args
    if not os.path.exists(args.out_root):
        os.makedirs(args.out_root, mode=0o755)
    dumpfile = open(os.path.join(args.out_root, "out.txt"), 'w') # record statistics

    available_datasets = {attr_name: getattr(datasets, attr_name) for attr_name in dir(datasets) if callable(getattr(datasets, attr_name))}
    dataloader = available_datasets[args.data_type](
        root=args.data_root,
        shuffle=True,
        augdict= edict({
            "augment": False,
            "augdgre": 90.0,
            "augdist": 5.0,
            "augjitr": 0.00,
            "augnois": 0
        }),
        args=args
    )

    model_conf = torch.load(args.extracter_weight)["config"]
    model_params = torch.load(args.extracter_weight)["state_dict"]
    feat_model = models.fcgf.load_model(args.fcgf_model)(
        1,
        model_conf["model_n_out"],
        bn_momentum=model_conf["bn_momentum"],
        conv1_kernel_size=model_conf["conv1_kernel_size"],
        normalize_feature=model_conf["normalize_feature"]
    )
    feat_model.load_state_dict(model_params)
    feat_model.eval()
    model_device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    feat_model.to(model_device)

    for i, (points1, points2, T_gdth, sample_name) in tqdm(enumerate(dataloader), total=len(dataloader), ncols=100, desc="gen npz"):
        # step1: voxel downsample
        downsampled_coords1, voxelized_coords1, idx_dse2vox1 = utils.voxel_down_sample_gpt(points1, args.voxel_size)
        downsampled_coords2, voxelized_coords2, idx_dse2vox2 = utils.voxel_down_sample_gpt(points2, args.voxel_size)

        # step2: detect key points using ISS
        keyptsdict1 = utils.iss_detect(downsampled_coords1, args.voxel_size * args.key_radius_factor, args.lambda1, args.lambda2)
        keyptsdict2 = utils.iss_detect(downsampled_coords2, args.voxel_size * args.key_radius_factor, args.lambda1, args.lambda2)
        keyptsidx1 = keyptsdict1["id"].values
        keyptsidx2 = keyptsdict2["id"].values
        if len(keyptsidx1) == 0 or len(keyptsidx2) == 0:
            utils.log_warn(f"{sample_name} failed to find ISS keypoints, continue to next sample")
            continue
        keyptsidx1 = add_salt(len(downsampled_coords1), keyptsidx1, args.salt_keypts)
        keyptsidx2 = add_salt(len(downsampled_coords2), keyptsidx2, args.salt_keypts)

        # step3: compute FCGF for each key point
        # compute all points' fcgf
        fcgfs1 = feat_model(
            ME.SparseTensor(
                coordinates=ME.utils.batched_coordinates([voxelized_coords1]).to(model_device), 
                features=torch.ones(len(downsampled_coords1), 1).to(model_device)
            )
        ).F.detach().cpu().numpy()
        fcgfs2 = feat_model(
            ME.SparseTensor(
                coordinates=ME.utils.batched_coordinates([voxelized_coords2]).to(model_device), 
                features=torch.ones(len(downsampled_coords2), 1).to(model_device)
            )
        ).F.detach().cpu().numpy()
        # only select key points' fcgf
        keyfcgfs1 = fcgfs1[keyptsidx1]
        keyfcgfs2 = fcgfs2[keyptsidx2]

        # step4: coarse ransac registration
        # use fpfh feature descriptor to compute matches
        matches = ransac.init_matches(keyfcgfs1.T, keyfcgfs2.T)
        keypts1 = downsampled_coords1[keyptsidx1]
        keypts2 = downsampled_coords2[keyptsidx2]
        correct = utils.ground_truth_matches(matches, keypts1, keypts2, args.voxel_size * 1.50, T_gdth)
        num_valid_matches = correct.astype(np.int32).sum()
        num_total_matches = correct.shape[0]
        tqdm.write(utils.log_info(f"gdth/init: {num_valid_matches:.2f}/{num_total_matches:.2f}={num_valid_matches/num_total_matches:.2f}", quiet=True))
        
        dumpfile.write(f"{sample_name} {num_valid_matches:d} {num_total_matches:d} {num_valid_matches/num_total_matches:.2f}\n")
    
    dumpfile.flush()
    dumpfile.close()

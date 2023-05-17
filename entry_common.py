import numpy as np
import open3d as o3d
from easydict import EasyDict as edict

from datasets import datasets
import config
import utils

import models


# step1: read point cloud pair
# step2: voxel down sample
# step3: extract ISS feature
# step4: feature description
# step5: RANSAC registration
#   step5.1: establish feature correspondences
#   step5.2: select n(> 3) pairs to solve the transformation R and t
#   step5.3: repeat step5.2 until error converge
# step6: ICP optimized transformation [R,t]

if __name__ == "__main__":
    args = config.args

    available_datasets = {attr_name: getattr(datasets, attr_name) for attr_name in dir(datasets) if callable(getattr(datasets, attr_name))}
    dataloader = available_datasets[args.data_type](
        root=args.data_root,
        shuffle=True,
        augdict= edict({
            "augment": True,
            "augdgre": 90.0,
            "augdist": 5.00,
            "augjitr": 0.00,
            "augnois": 0.00
        }),
        args=args
    )
    
    # model configurations
    detecter_conf = edict({
        "key_radius": args.voxel_size * args.key_radius_factor,
        "lambda1": args.lambda1,
        "lambda2": args.lambda2
    })
    extracter_conf = edict({
        "extracter_type": args.extracter_type,
        # for fcgf
        "extracter_weight": args.extracter_weight,
        "fcgf_model": args.fcgf_model,
        # for fpfh
        "feat_radius": args.voxel_size * args.fpfh_radius_factor,
        "feat_neighbour_num": args.fpfh_nn
    })
    ransac_conf = edict({
        "num_workers": 4,
        "num_samples": 8,
        "max_corrdist": args.voxel_size * 1.50,
        "num_iter": 7500,
        "num_vald": 750,
        "num_rfne": 25
    })
    checkr_conf = edict({
        "max_corrdist": args.voxel_size * 1.50,
        "mutldist_factor": 0.85,
        "normdegr_thresh": None
    })
    
    register = models.registercore.RansacRegister(
        voxel_size=args.voxel_size,
        # keypoint detector
        detecter_conf=detecter_conf,
        # feature extracter
        extracter_conf=extracter_conf,
        # inlier proposal
        mapper_conf=args.mapper_conf,
        predicter_conf=args.predicter_conf,
        
        # optimization
        ransac_conf=ransac_conf,
        checkr_conf=checkr_conf,
        
        misc=args
    )
    
    total = len(dataloader)
    for i, (points1, points2, T_gdth, sample_name) in enumerate(dataloader):
        utils.log_info(f"{i + 1:4d}/{total} {sample_name}")
        if args.recompute_norm:
            points1_o3d = utils.npy2o3d(points1)
            points1_o3d.estimate_normals(search_param=o3d.geometry.KDTreeSearchParamHybrid(radius=args.voxel_size * 2.0, max_nn=50))
            points1 = utils.o3d2npy(points1_o3d)
            points2_o3d = utils.npy2o3d(points2)
            points2_o3d.estimate_normals(search_param=o3d.geometry.KDTreeSearchParamHybrid(radius=args.voxel_size * 2.0, max_nn=50))
            points2 = utils.o3d2npy(points2_o3d)
        (
            fine_registrartion,
            downsampled_coords1, downsampled_coords2,
            keyptsidx1, keyptsidx2,
            totl_matches, gdth_matches,
            msicret
        ) = register.register(points1, points2, T_gdth)
        if fine_registrartion is None:
            utils.log_warn(f"fail to register {sample_name}")
            utils.dump_registration_result(
                args.out_root, "output",
                points1, points2,
                downsampled_coords1, keyptsidx1,
                downsampled_coords2, keyptsidx2,
                T_gdth, np.eye(4),
                gdth_matches
            )
            continue
        
        T_pred = fine_registrartion.transformation
        
        diff_raxis, diff_degr, diff_trans = utils.trans_diff(T_pred, T_gdth)
        
        utils.dump_registration_result(
            args.out_root, "output",
            points1, points2,
            downsampled_coords1, keyptsidx1,
            downsampled_coords2, keyptsidx2,
            T_gdth, T_pred,
            gdth_matches
        )

        utils.log_info(f"{i + 1:4d}/{total} {sample_name}")
